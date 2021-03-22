from nmigen import Elaboratable, Signal, Module, Memory, Cat
from alldigitalradio.shiftregisters import LinearFeedbackShiftRegister, GaloisCRC

class PacketParser(Elaboratable):
    def __init__(self, printer=None):
        self.bitstream = Signal()
        self.sample = Signal()
        self.currentbit = Signal()
        self.lfsr = LinearFeedbackShiftRegister()
        self.crc = GaloisCRC()
        self.done = Signal()
        self.debug = Signal()
        self.printer = printer
        self.state = Signal(5)
        self.crc_matches = Signal()

    def elaborate(self, platform):
        m = Module()
        m.submodules.lfsr = self.lfsr
        m.submodules.crc = self.crc

        if self.printer:
            payload_data = self.printer.mem
        else:
            payload_data = Memory(width=8, depth=64)

        m.submodules.payload_wport = wport = payload_data.write_port()

        header = Signal(16)
        header_idx = Signal(range(16))

        pdu = Signal(8)
        m.d.comb += pdu.eq(header[0:8])

        size = Signal(8)
        m.d.comb += size.eq(header[8:16])

        payload_read = Signal(12) # How many bits of the payload we've read

        payload_addr = Signal(48)
        payload_addr_idx = Signal(8)

        payload_sec_header_idx = Signal(4)
        payload_sec_header = Signal(16)
        payload_sec_size = Signal(8)
        payload_sec_type = Signal(8)
        m.d.comb += [
                payload_sec_size.eq(payload_sec_header[0:8]),
                payload_sec_type.eq(payload_sec_header[8:16])
        ]
        payload_sec_read = Signal(12)

        payload_byte = Signal(8)

        dewhitened = Signal()
        m.d.comb += dewhitened.eq(self.bitstream ^ self.lfsr.output)

        crc = Signal(24)
        crc_idx = Signal(8)
        crc_matches = self.crc_matches
        m.d.comb += crc_matches.eq(Cat([self.crc.crc[i] == crc[24 - i - 1] for i in range(24)]).all())

        should_print = Signal()

        with m.FSM() as fsm:
            # Exposed for debugging purposes
            m.d.comb += self.state.eq(fsm.state)

            with m.State("IDLE"):
                with m.If(self.sample):
                    m.next = "READ_HEADER"
                    m.d.sync += [
                            header_idx.eq(1),
                            header.eq(Cat(dewhitened, [0]*7)),
                            self.currentbit.eq(dewhitened),
                            self.lfsr.run_strobe.eq(1),

                            self.crc.input.eq(dewhitened),
                            self.crc.en.eq(1),

                            should_print.eq(0),
                    ]
                with m.Else():
                    # Reset goes high at the end
                    m.d.sync += [
                        self.lfsr.reset.eq(0),
                        self.crc.reset.eq(0)
                    ]

            with m.State("READ_HEADER"):
                with m.If(self.sample):
                    m.d.sync += [
                            header_idx.eq(header_idx + 1),
                            header.eq(header | (dewhitened << header_idx)),
                            self.currentbit.eq(dewhitened),
                            self.lfsr.run_strobe.eq(1),

                            self.crc.input.eq(dewhitened),
                            self.crc.en.eq(1),
                    ]
                    with m.If(header_idx == 15):
                        m.d.sync += [
                                payload_read.eq(0),
                                payload_addr.eq(0),
                                payload_addr_idx.eq(0)
                        ]
                        m.next = "READ_PAYLOAD_ADDR"
                with m.Else():
                    m.d.sync += [
                        self.lfsr.run_strobe.eq(0),
                        self.crc.en.eq(0)
                    ]

            with m.State("READ_PAYLOAD_ADDR"):
                with m.If(self.sample):
                    m.d.sync += [
                            payload_read.eq(payload_read + 1),
                            payload_addr_idx.eq(payload_addr_idx + 1),
                            payload_addr.eq(payload_addr | (dewhitened << payload_addr_idx)),
                            self.currentbit.eq(dewhitened),
                            self.lfsr.run_strobe.eq(1),

                            self.crc.input.eq(dewhitened),
                            self.crc.en.eq(1),
                    ]
                    with m.If(payload_addr_idx == (48 - 1)):
                        m.d.sync += [
                                payload_sec_header.eq(0),
                                payload_sec_header_idx.eq(0)
                        ]
                        m.next = "READ_PAYLOAD_SECTION_HEADER"
                with m.Else():
                    m.d.sync += [
                        self.lfsr.run_strobe.eq(0),
                        self.crc.en.eq(0)
                    ]

            with m.State("READ_PAYLOAD_SECTION_HEADER"):
                with m.If(self.sample):
                    m.d.sync += [
                            payload_read.eq(payload_read + 1),
                            payload_sec_header_idx.eq(payload_sec_header_idx + 1),
                            payload_sec_header.eq(payload_sec_header | (dewhitened << payload_sec_header_idx)),
                            self.currentbit.eq(dewhitened),
                            self.lfsr.run_strobe.eq(1),

                            self.crc.input.eq(dewhitened),
                            self.crc.en.eq(1),
                    ]
                    with m.If(payload_sec_header_idx == 15):
                        m.d.sync += payload_sec_read.eq(0)
                        m.next = "READ_PAYLOAD_SECTION_CONTENT"
                with m.Else():
                    m.d.sync += [
                        self.lfsr.run_strobe.eq(0),
                        self.crc.en.eq(0)
                    ]

                # If we previously were in READ_PAYLOAD_SECTION_CONTENT, null terminate
                # what we read
                m.d.comb += [
                    wport.addr.eq((payload_sec_read >> 3) + 1),
                    wport.en.eq(1),
                    wport.data.eq(0)
                ]

            with m.State("READ_PAYLOAD_SECTION_CONTENT"):
                with m.If(self.sample):
                    m.d.sync += [
                            payload_read.eq(payload_read + 1),
                            payload_sec_read.eq(payload_sec_read + 1),
                            self.currentbit.eq(dewhitened),
                            self.lfsr.run_strobe.eq(1),

                            self.crc.input.eq(dewhitened),
                            self.crc.en.eq(1),
                    ]

                    # m.d.comb += self.debug.eq(payload_sec_type == 0x9)

                    with m.If((payload_sec_type == 0x9) | (payload_sec_type == 0x8)): # If this section is a complete local name
                        idx = payload_read & 0x7
                        with m.If(idx == 0):
                            m.d.sync += payload_byte.eq(dewhitened)
                        with m.Else():
                            m.d.sync += payload_byte.eq(payload_byte | (dewhitened << idx))
                        with m.If(idx == 0b111):
                            m.d.comb += [
                                wport.addr.eq(payload_sec_read >> 3),
                                wport.en.eq(1),
                                wport.data.eq(payload_byte | (dewhitened << idx))
                            ]
                        m.d.sync += should_print.eq(should_print | 1)


                    with m.If((payload_read + 1) >= size << 3):
                        m.next = "READ_CRC"
                        m.d.sync += [
                            crc_idx.eq(0),
                            crc.eq(0)
                        ]

                    with m.Else():
                        with m.If((payload_sec_read + 1) == (payload_sec_size - 1) << 3):
                            m.d.sync += [
                                    payload_sec_header.eq(0),
                                    payload_sec_header_idx.eq(0)
                            ]
                            m.next = "READ_PAYLOAD_SECTION_HEADER"

                with m.Else():
                    m.d.sync += [
                        self.lfsr.run_strobe.eq(0),
                        self.crc.en.eq(0)
                    ]

            with m.State("READ_CRC"):
                with m.If(self.sample):
                    m.d.sync += [
                            crc_idx.eq(crc_idx + 1),
                            crc.eq(crc | (dewhitened << crc_idx)),
                            self.currentbit.eq(dewhitened),
                            self.lfsr.run_strobe.eq(1),

                            self.crc.en.eq(0),
                    ]
                    with m.If(crc_idx == 23):
                        m.next = "CHECK_CRC"
                with m.Else():
                    m.d.sync += [
                        self.lfsr.run_strobe.eq(0),
                        self.crc.en.eq(0)
                    ]

            with m.State("CHECK_CRC"):
                with m.If(crc_matches & should_print):
                    m.next = "START_READOUT"
                with m.Else():
                    m.next = "IDLE"
                    m.d.comb += self.done.eq(1)
                    m.d.sync += [
                        self.lfsr.run_strobe.eq(0),
                        self.lfsr.reset.eq(1),
                        self.crc.reset.eq(1)
                    ]
            with m.State("START_READOUT"):
                # If we previously were in READ_PAYLOAD_SECTION_CONTENT, null terminate
                # what we read
                m.d.comb += [
                    wport.addr.eq((payload_sec_read >> 3) + 1),
                    wport.en.eq(1),
                    wport.data.eq(0)
                ]

                m.d.comb += self.debug.eq(1)
                if self.printer:
                    m.d.comb += self.printer.start.eq(1)
                    m.next = "WAIT_READOUT"
                else:
                    m.d.comb += self.done.eq(1)
                    m.next = "IDLE"
            with m.State("WAIT_READOUT"):
                if self.printer:
                    with m.If(self.printer.done):
                        m.next = "IDLE"
                        m.d.comb += self.done.eq(1)
                        m.d.sync += [
                            self.lfsr.run_strobe.eq(0),
                            self.lfsr.reset.eq(1),
                            self.crc.reset.eq(1)
                        ]
                else:
                    # Invalid
                    pass


        return m