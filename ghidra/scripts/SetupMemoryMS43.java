// Pre-script for importing a 512 KB MS43 full read at 0x000000. The CPU's view
// (see ms43emu/machine.py): the flash at its offsets in segment 0 up to 0xBFFF
// and from 0x10000; segment 0 above 0xC000 is RAM, XRAM, CAN, ESFRs, IRAM, SFRs;
// 0x040000-0x041FFF is the external RAM (ADDRSEL2) over the flash; the program
// runs through the chip's mirror at 0x081000-0x0FFFFF (A19 ignored; 0x080000-
// 0x080FFF is the ADDRSEL3 device). DPP0-3 = 1C/3B/10/03 everywhere.
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.lang.Register;
import java.math.BigInteger;

public class SetupMemoryMS43 extends GhidraScript {
    Address a(long x) { return currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(x); }
    public void run() throws Exception {
        Memory mem = currentProgram.getMemory();
        MemoryBlock flash = mem.getBlock(a(0x000000));
        if (flash == null || flash.getSize() != 0x80000) throw new Exception("import the 512 KB read at base 0x000000");
        mem.split(flash, a(0x00C000));
        mem.split(mem.getBlock(a(0x00C000)), a(0x010000));
        mem.removeBlock(mem.getBlock(a(0x00C000)), monitor);             // blank in the read; RAM and SFRs on the chip
        mem.split(mem.getBlock(a(0x010000)), a(0x040000));
        mem.split(mem.getBlock(a(0x040000)), a(0x042000));
        mem.removeBlock(mem.getBlock(a(0x040000)), monitor);             // under the external RAM window
        mem.split(mem.getBlock(a(0x042000)), a(0x070000));
        mem.getBlock(a(0x000000)).setName("boot");
        mem.getBlock(a(0x010000)).setName("prog_lo");
        mem.getBlock(a(0x042000)).setName("prog_hi");
        mem.getBlock(a(0x070000)).setName("cal");
        for (MemoryBlock b : mem.getBlocks()) { b.setRead(true); b.setWrite(false); b.setExecute(!b.getName().equals("cal")); }
        MemoryBlock m = mem.createByteMappedBlock("mirror", a(0x081000), a(0x001000), 0x7F000, false);
        m.setRead(true); m.setExecute(true); m.setWrite(false);
        String[][] ram = { {"ERAM","40000","41FFF"}, {"XRAM","C000","E7FF"}, {"CAN","EF00","EFFF"}, {"ESFR","F000","F1FF"}, {"IRAM","F200","FDFF"}, {"SFR","FE00","FFFF"} };
        for (String[] r : ram) {
            long s = Long.parseLong(r[1], 16), e = Long.parseLong(r[2], 16);
            MemoryBlock b = mem.createUninitializedBlock(r[0], a(s), e - s + 1, false);
            b.setRead(true); b.setWrite(true); b.setExecute(false);
            b.setVolatile(r[0].equals("CAN") || r[0].endsWith("SFR"));
        }
        Register[] dpp = { currentProgram.getRegister("DPP0"), currentProgram.getRegister("DPP1"), currentProgram.getRegister("DPP2"), currentProgram.getRegister("DPP3") };
        long[] val = { 0x1C, 0x3B, 0x10, 0x03 };
        for (int i = 0; i < 4; i++) currentProgram.getProgramContext().setValue(dpp[i], a(0x000000), a(0x0FFFFF), BigInteger.valueOf(val[i]));
        println("MS43 memory set up; DPP0-3 = 1C/3B/10/03");
    }
}
