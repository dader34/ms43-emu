// Disassembles from each address given (following the flow) and prints the
// instructions in [start, start+len) for each "start:len" argument (hex).
// Usage: -postScript Listing.java 0E1192:80 0DFE0A:60
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
public class Listing extends GhidraScript {
    public void run() throws Exception {
        AddressSpace sp = currentProgram.getAddressFactory().getDefaultAddressSpace();
        for (String arg : getScriptArgs()) {
            String[] p = arg.split(":");
            long s = Long.parseLong(p[0], 16), n = p.length > 1 ? Long.parseLong(p[1], 16) : 0x40;
            Address start = sp.getAddress(s);
            if (getInstructionAt(start) == null) disassemble(start);
            println("=== " + arg);
            Address end = start.add(n);
            Instruction ins = getInstructionAt(start);
            if (ins == null) ins = getInstructionAfter(start);
            for (; ins != null && ins.getAddress().compareTo(end) < 0; ins = ins.getNext()) {
                if (ins.getNext() == null || !ins.getNext().getAddress().equals(ins.getMaxAddress().add(1))) {
                    Address after = ins.getMaxAddress().add(1);
                    if (after.compareTo(end) < 0 && getInstructionAt(after) == null && ins.getFlowType().hasFallthrough()) disassemble(after);
                }
                StringBuilder b = new StringBuilder();
                for (byte x : ins.getBytes()) b.append(String.format("%02x", x & 0xff));
                String refs = "";
                for (Reference r : ins.getReferencesFrom()) refs += " ->" + r.getToAddress();
                println(String.format("%s  %-10s %-36s%s", ins.getAddress(), b, ins, refs));
                Instruction nx = ins.getNext();
                if (nx == null || !nx.getAddress().equals(ins.getMaxAddress().add(1))) {
                    Address after = ins.getMaxAddress().add(1);
                    if (after.compareTo(end) < 0 && getInstructionAt(after) == null) { disassemble(after); }
                }
            }
        }
    }
}
