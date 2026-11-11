"""Only kill the owned process group when the parent pipe closes unexpectedly."""
import os, signal, sys, time
from pathlib import Path
def identity(pid):
    try: return Path('/proc/%d/stat'%pid).read_text().rsplit(')',1)[1].split()[19]
    except OSError: return None
if __name__=='__main__':
    pid=int(sys.argv[1]); stamp=identity(pid)
    if sys.stdin.buffer.readline()!=b'done\n' and stamp is not None and identity(pid)==stamp:
        try:
            os.killpg(pid,signal.SIGTERM); time.sleep(3)
            if identity(pid)==stamp: os.killpg(pid,signal.SIGKILL)
        except ProcessLookupError: pass
