import sys
import numpy as np

from nmigen import Elaboratable, Module, Memory, Signal, ClockSignal, signed, Instance
from nmigen.build import Resource, Pins, Attrs

from alldigitalradio.io.generic_serdes import get_serdes_implementation
import alldigitalradio.hardware as hardware

from alldigitalradio.mixer import SummingMixer
from alldigitalradio.filter import RunningBoxcarFilter
from alldigitalradio.trig import MagnitudeApproximator
from alldigitalradio.sync import CorrelativeSynchronizer
from alldigitalradio.symboltable import SymbolTable
from alldigitalradio.util import pack_mem

from onebitbt.parser import PacketParser
from onebitbt.clocking import Reference120MhzClock

from serialcommander.uart import UART
from serialcommander.commander import Commander
from serialcommander.printer import TextMemoryPrinter, BinarySignalPrinter, BinaryMemoryPrinter
from serialcommander.toggler import Toggler

# See research/Transmitter.ipynb to see how this is generated.
# This could easily be generated in software or gateware, but the feasability seems obvious so I
# instead precompute it
PACKET =[22, 21, 20, 21, 20, 21, 20, 21, 20, 23, 10, 12, 13, 12, 15, 2, 4, 7, 24, 16, 8, 2, 4, 7, 26, 27, 6, 5, 3, 9, 22, 21, 20, 23, 8, 2, 3, 9, 22, 23, 8, 0, 24, 18, 19, 25, 6, 7, 26, 28, 29, 27, 1, 14, 15, 2, 4, 7, 24, 18, 19, 30, 31, 16, 8, 2, 3, 14, 15, 2, 3, 9, 17, 30, 31, 18, 19, 25, 6, 5, 3, 14, 15, 2, 3, 14, 13, 12, 13, 11, 17, 30, 31, 18, 20, 21, 19, 25, 1, 9, 22, 21, 19, 30, 31, 16, 10, 12, 15, 0, 26, 27, 1, 9, 17, 30, 31, 18, 19, 25, 1, 14, 13, 12, 13, 11, 17, 30, 29, 28, 29, 27, 1, 14, 13, 11, 22, 23, 8, 0, 26, 27, 6, 5, 4, 7, 24, 16, 8, 2, 3, 9, 17, 25, 6, 7, 26, 28, 31, 16, 8, 0, 24, 18, 20, 21, 20, 23, 10, 11, 22, 21, 19, 25, 1, 14, 15, 0, 24, 16, 8, 2, 4, 5, 4, 7, 24, 18, 20, 23, 8, 2, 3, 9, 17, 30, 31, 16, 8, 0, 24, 18, 19, 30, 29, 28, 31, 18, 20, 21, 20, 23, 10, 11, 17, 30, 29, 28, 31, 16, 8, 0, 26, 27, 1, 14, 15, 0, 26, 27, 6, 7, 24, 18, 19, 25, 6, 7, 24, 16, 8, 0, 26, 28, 29, 27, 1, 9, 17, 25, 1, 9, 17, 30, 29, 28, 29, 27, 6, 5, 3, 14, 15, 0]

TABLE = np.load('data/gmsk_2402e6_6e9.npy').flatten()

class BLEAdvertiser(Elaboratable):
    def __init__(self):
        self.refclk = Signal()
        self.serdes = get_serdes_implementation()(line_rate=6.0e9, refclk_freq=120e6, internal_refclk=self.refclk)
        self.uart = UART(int(25e6/115200))

    def elaborate(self, platform):
        m = Module()

        output_enable = Toggler()
        m.submodules.uart = self.uart
        m.submodules.commander = Commander(self.uart, {
            '1': output_enable,
        })
        # Connect the UART ports to the real pins
        m.d.comb += [
            platform.request('uart_tx').eq(self.uart.tx_o),
            self.uart.rx_i.eq(platform.request('uart_rx'))
        ]

        m.submodules.serdes = serdes = self.serdes

        # The SERDES PLL is fairly coarse, as in it can only multiply
        # the reference clock frequency by a set number of integers (which
        # max out at 20 and 25). Given the board I'm using has a 125MHz
        # reference clock, that only allows 5GHz or 6.25GHz line rates.
        # We want higher than 5GHz because the nyquist sampling and we want
        # something that results in a samples per symbol rate that is evenly
        # divisible by 20. For a 1msps signal, the samples per symbol at 6.25GHz
        # is 6250, which is not divisible by 20. So instead, I synthesize a ref
        # clock at 120MHz which is then synthesized to a 6GHz line rate, resulting
        # in 6000 samples per symbol, which is evently divisible by 20.
        clk125 = Signal()
        m.submodules.clock = Instance('IBUFDS_GTE2',
            o_O=clk125,
            i_I=platform.request('clk_p', dir='-'),
            i_IB=platform.request('clk_n', dir='-'),
            i_CEB=0)
        m.submodules.ref120 = ref120 = Reference120MhzClock()
        m.d.comb += [
            ref120.clk125.eq(clk125),
            self.refclk.eq(ref120.clk120)
        ]

        # The symbol table reads the corresponding symbols for each
        # symbol in PACKET and outputs them on tx_data
        m.submodules.symboltable = symboltable = SymbolTable(
            table=pack_mem(TABLE, width=20), 
            packet=Memory(width=8, depth=len(PACKET), init=PACKET), 
            samples_per_symbol=int(6e9/1e6), 
            tx_domain="tx")
        m.d.comb += symboltable.packet_length.eq(len(PACKET))

        # Maker the output toggleable over serial
        with m.If(~output_enable.output):
            m.d.comb += serdes.tx_data.eq(symboltable.tx_data)
        with m.Else():
            m.d.comb += serdes.tx_data.eq(0)
        m.d.comb += platform.request("led").eq(~output_enable.output)

        # Quick state machine to transmit and then wait a bit before
        # transmitting again
        counter = Signal(32)
        with m.FSM():
            with m.State("START"):
                m.d.sync += symboltable.tx_reset.eq(1)
                m.next = "WAIT_DONE"
            with m.State("WAIT_DONE"):
                m.d.sync += symboltable.tx_reset.eq(0)
                with m.If(symboltable.tx_done):
                    m.d.sync += counter.eq(0)
                    m.next = "PAUSE"
            with m.State("PAUSE"):
                m.d.sync += counter.eq(counter + 1)
                with m.If(counter >= int(1e4)):
                    m.next = "START"
        return m

if __name__ == '__main__':
    with hardware.use(sys.argv[1]) as platform:
        platform().build(BLEAdvertiser(), do_program=True)