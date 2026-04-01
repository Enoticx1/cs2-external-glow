import ctypes, ctypes.wintypes, struct, time, threading, sys

GLOW_COLOR  = (1.0, 0.0, 0.0)   
TEAM_CHECK  = True              

kernel32 = ctypes.windll.kernel32

PROCESS_ALL_ACCESS   = 0x1F0FFF
TH32CS_SNAPPROCESS   = 0x00000002
TH32CS_SNAPMODULE    = 0x00000008
TH32CS_SNAPMODULE32  = 0x00000010

# Offsets  (update with a2x https://github.com/a2x/cs2-dumper/blob/main/output/offsets.hpp)
dwEntityList      = 0x24B0258
dwLocalPlayerPawn = 0x206A9E0

m_iHealth    = 0x354
m_lifeState  = 0x35C
m_iTeamNum   = 0x3F3
m_hPawn      = 0x6C4
m_Glow       = 0xCC0


class Memory:
    def __init__(self):
        self.handle = None
        self.pid    = None
        self.client = 0
        self._rd    = ctypes.c_size_t(0)

    def open(self, proc="cs2.exe"):
        class PE32(ctypes.Structure):
            _fields_ = [
                ("dwSize",             ctypes.wintypes.DWORD),
                ("cntUsage",           ctypes.wintypes.DWORD),
                ("th32ProcessID",      ctypes.wintypes.DWORD),
                ("th32DefaultHeapID",  ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID",       ctypes.wintypes.DWORD),
                ("cntThreads",         ctypes.wintypes.DWORD),
                ("th32ParentProcessID",ctypes.wintypes.DWORD),
                ("pcPriClassBase",     ctypes.wintypes.LONG),
                ("dwFlags",            ctypes.wintypes.DWORD),
                ("szExeFile",          ctypes.c_char * 260),
            ]
        sn = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        pe = PE32(); pe.dwSize = ctypes.sizeof(PE32)
        if kernel32.Process32First(sn, ctypes.byref(pe)):
            while True:
                if pe.szExeFile.decode("utf-8", "ignore").lower() == proc:
                    self.pid = pe.th32ProcessID; break
                if not kernel32.Process32Next(sn, ctypes.byref(pe)): break
        kernel32.CloseHandle(sn)
        if not self.pid: return False
        self.handle = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, self.pid)
        return bool(self.handle)

    def get_module(self, name="client.dll"):
        class ME32(ctypes.Structure):
            _fields_ = [
                ("dwSize",       ctypes.wintypes.DWORD),
                ("th32ModuleID", ctypes.wintypes.DWORD),
                ("th32ProcessID",ctypes.wintypes.DWORD),
                ("GlblcntUsage", ctypes.wintypes.DWORD),
                ("ProccntUsage", ctypes.wintypes.DWORD),
                ("modBaseAddr",  ctypes.POINTER(ctypes.c_byte)),
                ("modBaseSize",  ctypes.wintypes.DWORD),
                ("hModule",      ctypes.wintypes.HMODULE),
                ("szModule",     ctypes.c_char * 256),
                ("szExePath",    ctypes.c_char * 260),
            ]
        sn = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, self.pid)
        me = ME32(); me.dwSize = ctypes.sizeof(ME32); result = 0
        if kernel32.Module32First(sn, ctypes.byref(me)):
            while True:
                if me.szModule.decode("utf-8", "ignore").lower() == name:
                    result = ctypes.cast(me.modBaseAddr, ctypes.c_void_p).value; break
                if not kernel32.Module32Next(sn, ctypes.byref(me)): break
        kernel32.CloseHandle(sn)
        return result

    def read(self, addr, size):
        buf = (ctypes.c_char * size)()
        kernel32.ReadProcessMemory(self.handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(self._rd))
        return bytes(buf)

    def u64(self, addr):
        b = self.read(addr, 8)
        return struct.unpack_from("<Q", b)[0] if len(b) >= 8 else 0

    def u32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack_from("<I", b)[0] if len(b) >= 4 else 0

    def write(self, addr, data: bytes):
        buf = (ctypes.c_char * len(data))(*data)
        kernel32.WriteProcessMemory(self.handle, ctypes.c_void_p(addr), buf, len(data), ctypes.byref(self._rd))


def apply_glow(mem, pawn, r, g, b):
    gp = pawn + m_Glow
    # float RGB at +0x8, +0xC, +0x10
    mem.write(gp + 0x30, struct.pack("<i", 3))       
    # packed RGBA: R | G<<8 | B<<16 | A<<24  (little-endian = RGBA bytes)
    mem.write(gp + 0x40, struct.pack("<BBBB",
        int(r*255), int(g*255), int(b*255), 255))
    mem.write(gp + 0x50, b"\x01\x01")


def glow_loop(mem):
    r, g, b = GLOW_COLOR
    print("[*] glow running")
    while True:
        try:
            el = mem.u64(mem.client + dwEntityList)
            if not el:
                time.sleep(0.05); continue

            lp = mem.u64(mem.client + dwLocalPlayerPawn)
            lt = 0
            if lp:
                raw = mem.read(lp + m_iTeamNum, 1)
                lt = raw[0] if raw else 0

            for i in range(1, 65):
                try:
                    le   = mem.u64(el + 0x10 + 8 * (i >> 9))
                    if not le: continue
                    ctrl = mem.u64(le + 0x70 * (i & 0x1FF))
                    if not ctrl: continue
                    ph   = mem.u32(ctrl + m_hPawn)
                    if not ph: continue
                    idx  = ph & 0x7FFF
                    pe   = mem.u64(el + 0x10 + 8 * (idx >> 9))
                    if not pe: continue
                    pawn = mem.u64(pe + 0x70 * (idx & 0x1FF))
                    if not pawn or pawn == lp: continue

                    raw = mem.read(pawn + m_iHealth, 0xA0)
                    if len(raw) < 0xA0: continue
                    hp   = struct.unpack_from("<i", raw, 0)[0]
                    ls   = raw[8]
                    team = raw[0x9F]

                    if hp <= 0 or ls != 0: continue
                    if TEAM_CHECK and team == lt: continue

                    apply_glow(mem, pawn, r, g, b)
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(0.002)


def main():
    print("CS2 External Glow https://github.com/Enoticx1/cs2-external-glow\n")

    mem = Memory()
    if not mem.open():
        print("[-] cs2.exe not found"); input(); return
    print(f"[+] cs2.exe found        (pid: {mem.pid})")

    mem.client = mem.get_module()
    if not mem.client:
        print("[-] client.dll not found"); input(); return
    print(f"[+] client.dll found     (base: 0x{mem.client:X})")

    print(f"[+] glow color           (r={GLOW_COLOR[0]:.2f} g={GLOW_COLOR[1]:.2f} b={GLOW_COLOR[2]:.2f})")
    print(f"[+] team check           {'on' if TEAM_CHECK else 'off'}")
    glow_loop(mem)


if __name__ == "__main__":
    main()
