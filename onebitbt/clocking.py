from nmigen import Elaboratable, Instance, Signal, Module, ClockDomain, ClockSignal

class ClockDivider4(Elaboratable):
    def __init__(self, indomain, outdomain):
        self.indomain = indomain
        self.outdomain = outdomain

    def elaborate(self, platform):
        # For virtual platforms just use a regular counter
        if platform is None:
            m = Module()

            counter = Signal(2)

            domain = getattr(m.d, self.indomain)
            domain += counter.eq(counter + 1)

            m.domains += ClockDomain(self.outdomain, reset_less=True)
            m.d.comb += ClockSignal(self.outdomain).eq(counter[1])

            return m

        m = Module()

        clkin = Signal() # Buffered output of input clock, into PLL
        clkfbout = Signal() # Unbuffered feedback out of PLL
        clkfbout_buf = Signal() # Buffered feedback into PLL
        clkout = Signal() # Unbuffered output from PLL
        div4clk = Signal() # Buffered output of output clock

        m.submodules.clockdiv = Instance("PLLE2_ADV", 
            p_BANDWIDTH="OPTIMIZED",
            p_COMPENSATION="ZHOLD",
            p_STARTUP_WAIT="FALSE",
            p_DIVCLK_DIVIDE=1,
            p_CLKFBOUT_MULT=4,
            p_CLKFBOUT_PHASE=0.000,
            p_CLKOUT0_DIVIDE=16,
            p_CLKOUT0_PHASE=0.000,
            p_CLKOUT0_DUTY_CYCLE=0.500,
            p_CLKIN1_PERIOD=4.000,
        
            o_CLKFBOUT=clkfbout,
            o_CLKOUT0=clkout,
            i_CLKFBIN=clkfbout_buf,
            i_CLKIN1=clkin,

            i_CLKIN2=0,
            i_CLKINSEL=1,
            i_DADDR=0,
            i_DCLK=0,
            i_DEN=0,
            i_DI=0,
            i_DWE=0,
            i_PWRDWN=0,
            i_RST=0
        )

        m.submodules.inbuf = Instance("BUFG",
            i_I=ClockSignal(self.indomain),
            o_O=clkin
        )

        m.submodules.outbuf = Instance("BUFG",
            i_I=clkout,
            o_O=div4clk
        )

        m.submodules.clockfb = Instance("BUFG",
            i_I=clkfbout,
            o_O=clkfbout_buf)

        m.domains += ClockDomain(self.outdomain, reset_less=True)
        m.d.comb += ClockSignal(self.outdomain).eq(div4clk)

        return m