import sys

from nmigen import Elaboratable, Module, Memory, Signal, ClockSignal, signed
from nmigen.build import Resource, Pins, Attrs

from alldigitalradio.io.generic_serdes import get_serdes_implementation
import alldigitalradio.hardware as hardware

from alldigitalradio.mixer import SummingMixer
from alldigitalradio.filter import RunningBoxcarFilter
from alldigitalradio.trig import MagnitudeApproximator
from alldigitalradio.sync import CorrelativeSynchronizer

from onebitbt.parser import PacketParser
from onebitbt.clocking import ClockDivider4

from serialcommander.uart import UART
from serialcommander.commander import Commander
from serialcommander.printer import TextMemoryPrinter, BinarySignalPrinter, BinaryMemoryPrinter
from serialcommander.toggler import Toggler

class SimpleDecimator(Elaboratable):
    def __init__(self, decimation_factor=None, max_val=20, domain="sync"):
        self.decimation_factor = decimation_factor
        self.domain = domain

        self.input = Signal(signed(14))
        self.output = Signal(signed(20))

        self.running_sum = Signal(signed(20))
        self.counter = Signal(signed(8))

    def elaborate(self, platform):
        m = Module()

        domain = getattr(m.d, self.domain)
        with m.If(self.counter == self.decimation_factor - 1):
            domain += [
                self.counter.eq(0),
                self.output.eq(self.running_sum + self.input),
                self.running_sum.eq(0)
            ]
        with m.Else():
            domain += [
                self.counter.eq(self.counter + 1),
                self.running_sum.eq(self.running_sum + self.input)
            ]

        return m

class BLERadio(Elaboratable):
    def __init__(self):
        self.serdes = get_serdes_implementation()()
        self.uart = UART(int(25e6/115200))

    def elaborate(self, platform):
        m = Module()
        m.submodules.serdes = serdes = self.serdes

        # Set up a UART and a printer (and directly connectt the printer to the UART)
        m.submodules.uart = uart = self.uart
        m.submodules.printer = printer = TextMemoryPrinter(Memory(width=8, depth=32), 32)
        if platform:
            m.d.comb += [
                platform.request('uart_tx').eq(uart.tx_o),
                uart.rx_i.eq(platform.request('uart_rx')),
            ]

        debug = False
        if not debug:
            m.d.comb += [
                uart.tx_data.eq(printer.tx_data),
                uart.tx_rdy.eq(printer.tx_rdy),
                printer.tx_ack.eq(uart.tx_ack),
            ]

        # Set up a clock divider on the RX clock because we can't do everything at 250MHz
        m.submodules.clockdivider = ClockDivider4("rx", "rxdiv4")

        # Mix the incoming data down to BB
        m.submodules.mixerHigh = mixerHigh = SummingMixer(sample_rate=5e9, frequency=2.40225e9, max_error=0.0001, domain="rx")
        m.submodules.mixerLow = mixerLow = SummingMixer(sample_rate=5e9, frequency=2.40175e9, max_error=0.0001, domain="rx")
        m.d.comb += [
            mixerHigh.input.eq(serdes.rx_data),
            mixerLow.input.eq(serdes.rx_data)
        ]

        # Now add low pass filters on the outputs
        width = 64
        m.submodules.lpfHighI = lpfHighI = RunningBoxcarFilter(width, domain="rxdiv4")
        m.submodules.lpfHighQ = lpfHighQ = RunningBoxcarFilter(width, domain="rxdiv4")
        m.submodules.lpfLowI = lpfLowI = RunningBoxcarFilter(width, domain="rxdiv4")
        m.submodules.lpfLowQ = lpfLowQ = RunningBoxcarFilter(width, domain="rxdiv4")
        m.d.comb += [
            lpfHighI.input.eq(mixerHigh.outputIsum),
            lpfHighQ.input.eq(mixerHigh.outputQsum),
            lpfLowI.input.eq(mixerLow.outputIsum),
            lpfLowQ.input.eq(mixerLow.outputQsum),
        ]

        # Next compute the magnitude of the low-pass-filtered I and Q
        # These are fully combinatoric so don't need a domain specified
        m.submodules.highMag = highMag = MagnitudeApproximator()
        m.submodules.lowMag = lowMag = MagnitudeApproximator()
        m.d.comb += [
            highMag.inputI.eq(lpfHighI.output),
            highMag.inputQ.eq(lpfHighQ.output),
            lowMag.inputI.eq(lpfLowI.output),
            lowMag.inputQ.eq(lpfLowQ.output),
        ]

        m.submodules.decimator = decimator = SimpleDecimator(decimation_factor=4,domain="rxdiv4")
        m.d.comb += decimator.input.eq(highMag.magnitude - lowMag.magnitude)

        # Finally, compare the two magnitudes
        # (We need to pipeline this a bit to meet timing)
        lowMagOut = Signal(32)
        highMagOut = Signal(32)
        basebandFast = Signal()
        baseband = Signal()
        m.d.rxdiv4 += [
            lowMagOut.eq(lowMag.magnitude),
            highMagOut.eq(highMag.magnitude),
            basebandFast.eq(lowMagOut > highMagOut)
        ]
        m.d.sync += [
            baseband.eq(decimator.output > 0)
        ]

        # Synchronize by looking for the start of an advertizing packet
        pattern = [
        0, 1, 0, 1, 0, 1, 0, 1, # Training sequence
        0, 1, 1, 0, 1, 0, 1, 1, # Advertizing access address
        0, 1, 1, 1, 1, 1, 0, 1,
        1, 0, 0, 1, 0, 0, 0, 1,
        0, 1, 1, 1, 0, 0, 0, 1]
        m.submodules.synchronizer = synchronizer = CorrelativeSynchronizer(pattern, samples_per_symbol=25)
        m.d.comb += synchronizer.input.eq(baseband)

        # Now parse the synchronized data
        m.submodules.parser = parser = PacketParser(printer=printer)
        m.d.comb += [
            parser.sample.eq(synchronizer.sample_strobe),
            parser.bitstream.eq(baseband),
            synchronizer.reset.eq(parser.done)
        ]

        if debug:
            toggler = Toggler()
            width = 1

            analyzer = Memory(width=width, depth=1024)
            m.submodules.wport = wport = analyzer.write_port(domain="rxdiv4")

            analyzer_in = Signal(width)
            analyzer_idx = Signal(range(1024))
            m.d.rxdiv4 += [
                analyzer_idx.eq(analyzer_idx + 1),
                analyzer_in.eq(basebandFast),
            ]
            m.d.comb += [
                wport.addr.eq(analyzer_idx),
                wport.data.eq(analyzer_in),
                wport.en.eq(toggler.output)
            ]

            m.submodules.commander = Commander(uart, {
                '1': toggler,
                '2': BinarySignalPrinter(serdes.rx_data),
                '3': BinaryMemoryPrinter(analyzer, width, 1024),
            })
            if platform:
                m.d.comb += [
                    platform.request('led').eq(toggler.output),
                    platform.request('debug1').eq(baseband),
                    platform.request('debug2').eq(synchronizer.matcher.match)
                ]

        return m

if __name__ == '__main__':
    with hardware.use(sys.argv[1]) as platform:
        platform().build(BLERadio(), do_program=True)