# onebitbt - A Bluetooth Low Energy Radio using SERDES as a 1-bit ADC/DAC

This is a proof-of-concept Bluetooth receiver that can receive bluetooth (advertising) packets using an FPGA and an antenna -- no ADC, filters, mixers, AGC or even amplifiers required. Just straight RF into a SERDES port sampling at 5Ghz. It's written in [nmigen](https://github.com/nmigen/nmigen), but can be compiled to verilog for usage elsewhere.

# ...why?

I wanted to prove to myself that I knew enough about RF to interface with a device I actually own (i.e. my phone), using a stack I wrote _from scratch_. After considering various protocols such as WiFi, LoRa, etc, I concluded that Bluetooth (Low Energy) would be easiest because:

1. There's generally one dominant modulation type used (GMSK) as compared to a bajillion for the various 802.11/WiFi standards.
2. In the default case, there's no forward error correction, so I wouldn't have to write a viterbi decoder or anything like it in hardware.
3. I have multiple decives that can easily transmit or receive bluetooth packets.

I could use one of the many SDRs I own, combined with GNURadio to write a packet parser but SDRs generally have built-in (configurable) direct conversion front-ends which take care of much of the "hard" parts of building a radio (read: the analog bits, which are quite finnicky). Assembling a circuit that had all the necessary mixers, amplifiers, oscillators seemed like a lot of work. After reading 30+ papers and PhD theses I hyopthesized I could do without all of this. To my surprise, this hypothesis was correct!

# How do I run this?

You'll first need to check out this code and install the dependencies (I recommend inside a python 3 virtual environment)
```
pip install numpy scipy matplotlib jupyterlab
pip install git+https://github.com/nmigen/nmigen
pip install git+https://github.com/newhouseb/serialcommander
pip install git+https://github.com/newhouseb/alldigitalradio
```
Next, clone this repository. 

## Running on simulated hardware

In order to runn the full thing at a reasonable speed, I use `iverilog` to compile the design to C++ which runs infinitely faster than the built-in nmigen simulation. Assuming you have `iverilog` installed, running is as simple as:

```
> python -m onebitbt.radio virtual data/bt1bit.txt
[...tons of compilation removed...]
Reading ../data/bt1bit.txt
Argon
Simulation Complete!
```

Once this is done you'll have a (huge, 1.3GB) VCD file at `build/sim.vcd` that you can inspect. This command also prints out any bytes sent by the UART ("Argon" in this case which is received the name of the device).

![image](https://user-images.githubusercontent.com/77915/112074130-effbd980-8b4b-11eb-825a-0722bfd1bd66.png)

## Running on real hardware

If you have the specific board I've been using (a TE0714 with a TEBB0714 carrier with a TE0790-03 programmer) then assuming you have `vivado` somewhere in your path, run from this repos root directory:

```
python -m onebitbt.radio te0714
```

Next, if you connect to the built-in serial port on the programmer (at 115200 baud) you'll see the names of nearby advertising devices printed out.

```
I LOVE MINDY
I LOVE MINDY
[TV] Samsung 6 Series (65)
I LOVE MINDY
```

If you don't have anything advertising and you have an iPhone you can download an app called "BLE Scanner" that has an "Advertiser" tab that allows your phone to advertise a bunch of random things.

# How does this work?

The chief realization here is that there are a large class of commonly used wireless protocols that encode their data in the phase/frequency of a radio wave, and _not_ the amplitude. Modulation types that fall into this include: BPSK, QPSK, FSK, GMSK and others which are used in things like (low-end) Wi-Fi, bluetooth, LoRa. If you don't need to measure the amplitude, then all you need to measure is when the radio waveform crosses zero. 

Second, high-speed communication between ICs/chips often use differential signalling, where rather than representing a one or zero as a voltage above or below a set threshold, a bit is sent by controlling a _pair_ of wires. If one voltage is greater than the other, that may be a one bit. If it's the opposite, it may be a zero bit. In order to build this, chips must integrate a high-speed comparator. By grounding one of these wires, we can then use the other wire to measure whether or not a signal is above or below zero (ground).

The final realization is that the industry has realized it's easier (up to a point) to build _one_ very high speed pair rather than _many_ parallel wires when communicating between chips (largely because parallel signals will have crosstalk). So rather than having 20 wires at 250 Mhz, it's better to just do 1 pair at 5 Ghz. Once we're sending bits at 5Ghz... this is above the required speed to sample 2.4Ghz WiFi/Bluetooth and most other consumer (non 5G mmWave or 5G WiFi) radio signals (using the standard Nyquist criterion -- which doesn't always hold, but this is a story for another time...).

So if we can sample a waveform at 1-bit of precision at 5Ghz then we can do all the traditional radio bits (i.e. downconversion) digitally in an FPGA. Naturally, digitally mixing a signal at 5GHz sounds quite expensive, but when your signals have only 1 bit of precision, multiplication reduces down to basic boolean logic which can be quite efficient. We can use this to convert the signal down to baseband and then from there on out we're into pretty standard FPGA DSP territory.

But you don't have to read all this prose, to explain (some of this) I've written a number of python notebooks that walk through the "research"

- [Detection](https://github.com/newhouseb/onebitbt/blob/master/research/Detection.ipynb) - (Covers) modulation and demodulation  of the waveform to bits, as well as an evaluation of the demodulation performance versus more traditional methods.
- [Parsing](https://github.com/newhouseb/onebitbt/blob/master/research/Parsing.ipynb) - Dewhitening, parsing and checking the bits for correctness.

For non-Bluetooth specific radio building blocks, you can refer to the notebooks in the [alldigitalradio](https://github.com/newhouseb/alldigitalradio) repository:

- [Downconversion](https://github.com/newhouseb/alldigitalradio/blob/main/research/Downconversion.ipynb) - Conversion from a carrier-modulated signal to baseband (with 1-bit signals)
- [Filtering](https://github.com/newhouseb/alldigitalradio/blob/main/research/Filtering.ipynb) - Comically simple filtering.
- [Synchronization](https://github.com/newhouseb/alldigitalradio/blob/main/research/Synchronization.ipynb) - Symbol synchronization.
- [Trigonometry](https://github.com/newhouseb/alldigitalradio/blob/main/research/Trigonometry.ipynb) - Computing the magnitude of a complext signal.
- [Shift Registers](https://github.com/newhouseb/alldigitalradio/blob/main/research/ShiftRegisters.ipynb) - Shift registers underpinning whitening and CRC checking.

# FAQ

## How good is this radio?

Honestly, it's not fantastic, but this isn't because of the precision, rather because I'm demodulating GMSK as if it was FSK.

![image](https://user-images.githubusercontent.com/77915/112077627-4c162c00-8b53-11eb-93d1-ed6dd9bcaf4b.png)

- Blue - Traditional demodulation (read: taking the derivative of the phase of the baseband signal)
- Orange - Traditional demoduation of a signal rounded to -1 and 1
- Green - FSK demodulation in simulated hardware
- Red - FSK demodulation in numpy.

Once I implement CORDIC to estimate the phase, we should get performance closer to the more traditional implementations.

## What about the transmitter?

I've built [a transmitter](https://twitter.com/newhouseb/status/1352796299700162560) as well, but the interest in the receive was far greater so I've started there. Will integrate the transmitter here in due time.

## Something is incorrect!

Please let me know! Twitter (@newhouseb) or GitHub is fine. I've been engineering in a cave and have no professional experience in this space, so I'm sure there are errors in addition to random bugs.

# System Diagram
```
                x     x
                 x   x
                  x x
                   │
                   │  Antenna
                   │
                   │
                   │
                   │
           Open    │
             │     │
        ┌────┴─────┴──────┐
        │   RX_N  RX_P    │
        │                 │
        │    SERDES RX    │
        │    @ 5 GSPS     │
        │                 │
        │       OUT       │
        └────────┬────────┘
                 │ 20bits wide at 250MHz
     ┌───────────┤
     │           │        Summing Mixer @ 2.40175 GHz
     │  ┌────────┼───────────────────────────────────┐
     │  │        │                                   │
     │  │        └────────┬────────┐                 │
     │  │                 │        │                 │
     │  │                 │        │                 │
     │  │   ┌──────┐      │        │      ┌──────┐   │
     │  │   │      │    ┌─┴┐      ┌┴─┐    │      │   │
     │  │   │  ~   ├────┤X │      │X ├────┤  ~   │   │
     │  │   │      │    └─┬┘      └┬─┘    │      │   │
     │  │   └──────┘      │        │      └──────┘   │
     │  │  One Bit        │        │     One Bit     │
     │  │  Oscillator     │        │     Oscillator  │
     │  │  0deg phase     │I      Q│     90deg phase │
     │  │            ┌────┘        └─────┐           │
     │  │      ┌─────┤                   ├─────┐     │
     │  │      │ +   │   Running adders  │ +   │     │
     │  │      │     │◄──of last 4    ──►│     │     │
     │  │      └──┬──┘   samples         └──┬──┘     │
     │  │         │                         │        │
     │  └─────────┼─────────────────────────┼────────┘
     │            │                         │
     │            │                         └─────────────┐
     │            │                                       │
     │            └─────────────────────────────────────┐ │
     │                                                  │ │
     └───────────┐                                      │ │
                 │                                      │ │
                 │        Summing Mixer @ 2.40225 GHz   │ │
        ┌────────┼───────────────────────────────────┐  │ │
        │        │                                   │  │ │
        │        └────────┬────────┐                 │  │ │
        │                 │        │                 │  │ │
        │                 │        │                 │  │ │
        │   ┌──────┐      │        │      ┌──────┐   │  │ │
        │   │      │    ┌─┴┐      ┌┴─┐    │      │   │  │ │
        │   │  ~   ├────┤X │      │X ├────┤  ~   │   │  │ │
        │   │      │    └─┬┘      └┬─┘    │      │   │  │ │
        │   └──────┘      │        │      └──────┘   │  │ │
        │  One Bit        │        │     One Bit     │  │ │
        │  Oscillator     │        │     Oscillator  │  │ │
        │  0deg phase     │I      Q│     90deg phase │  │ │
        │            ┌────┘        └─────┐           │  │ │
        │      ┌─────┤                   ├─────┐     │  │ │
        │      │ +   │   Running adders  │ +   │     │  │ │
        │      │     │◄──of last 4    ──►│     │     │  │ │
        │      └──┬──┘   samples         └──┬──┘     │  │ │
        │         │                         │        │  │ │
        └─────────┼─────────────────────────┼────────┘  │ │
                  │                         │           │ │
              ┌───┘   ┌─────────────────────┘           │ │
              │       │                  ┌──────────────┘ │
              │       │                  │                │
              │       |   @ 62.5MHz      │      ┌─────────┘
              │       │                  │      │
           ┌──┴─┐  ┌──┴─┐ 64 sample   ┌──┴─┐  ┌─┴──┐
           │ +  │  │ +  │ wide boxcar │ +  │  │ +  │
           │    │  │    │ filters     │    │  │    │
           └──┬─┘  └──┬─┘             └──┬─┘  └─┬──┘
              │       │                  │      │
              │       │                  │      │
              └┐    ┌─┘                  └┐    ┌┘
               │    │                     │    │
             ┌─┴────┴─┐                 ┌─┴────┴─┐
             │        │                 │        │
             │        │  Magnitude      │        │
             │  |s|   │  Approximators  │   |s|  │
             │        │                 │        │
             └───┬────┘                 └───┬────┘
                 └────────────┐  ┌──────────┘
                              │  │
                            ┌─┴──┴─┐
                            │      │
                            │  <   │  Comparator
                            │      │
                            └──┬───┘
                               │
                               │
                               │    Packet State Machine
           ┌───────────────────┼─────────────────┐
           │                   │                 │
           │                ┌──┴──┐              │
           │    Preamble    │     │              │
           │    Matching &  │  =  │              │
           │    Symbol Sync │     │              │
           │                └──┬──┘              │
           │                   │@1MHz            │
           │                ┌──┴──┐              │
           │    Dewhitening │     │              │
           │    LFSR        │  ^  │              │
           │                │     │              │
           │                └──┬──┘              │
           │       ┌─────┐     │    ┌─────┐      │
           │       │     ├─────┴────┤     │      │
           │       │CRC  │          │BRAM │      │
           │       │     │          │     │      │
           │       └─────┘          └──┬──┘      │
           │                           │         │
           └───────────────────────────┼─────────┘
                                       │
                                   ┌───┴────┐
                                   │        │
                                   │ UART   │
                                   │        │
                                   └───┬────┘
                                       │
                                       │

                                    Output
```
