// Prints the disassembly listing and decompiled C of the function at arg0 (hex address).
// Used to inspect patched images without re-exporting the whole program.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
public class DecompOne extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        Address ad = currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(Long.parseLong(args[0], 16));
        if (getInstructionAt(ad) == null) disassemble(ad);
        Function f = getFunctionAt(ad);
        if (f == null) f = createFunction(ad, "fn_" + args[0]);
        println("=== " + f.getName() + " @ " + ad + " body " + f.getBody().getMinAddress() + "-" + f.getBody().getMaxAddress());
        for (Instruction ins = getInstructionAt(ad); ins != null && f.getBody().contains(ins.getAddress()); ins = ins.getNext()) {
            StringBuilder b = new StringBuilder();
            for (byte x : ins.getBytes()) b.append(String.format("%02x", x & 0xff));
            println(String.format("%s  %-10s %s", ins.getAddress(), b, ins));
        }
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        DecompileResults r = di.decompileFunction(f, 60, monitor);
        println(r.getDecompiledFunction().getC());
        di.dispose();
    }
}
