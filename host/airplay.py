"""Per-app AirPlay via the same official Music API as esp32-apple-music-macmini."""
from pathlib import Path
import hashlib
import json
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / '.runtime/airplay.json'


def music(data):
    if data['action'] in ('select', 'play'):
        uid = str(data['uid'])
        if not uid.isdigit(): raise ValueError('无效 AirPlay 输出标识')
        result = subprocess.run(['/usr/bin/osascript', str(ROOT/'host/airplay_select.applescript'), uid],
            capture_output=True, text=True, timeout=20)
        if result.returncode: raise RuntimeError(result.stderr.strip() or 'AirPlay 连接失败')
        if data['action'] == 'select': return {'ok':True}
        p = subprocess.run(['/usr/bin/osascript',str(ROOT/'host/airplay_play.applescript'),
            str(data['path']), str(data['volume']), str(data['loop']).lower()],
            capture_output=True,text=True,timeout=20)
        if p.returncode: raise RuntimeError(p.stderr.strip() or 'AirPlay 播放失败')
        return {'ok':True,'id':p.stdout.strip()}
    p = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', str(ROOT/'host/airplay.js'),
                        json.dumps(data, ensure_ascii=False)], capture_output=True, text=True, timeout=20)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or 'Music AirPlay 操作失败')
    return json.loads(p.stdout)


def outputs():
    return music({'action':'outputs'})['items']


class AirPlayPlayer:
    def __init__(self):
        self.uid = ''
        self.track_id = None
        self.error = ''
        self._generation = 0
        self._guard = threading.RLock()
        try:
            self.uid = str(json.loads(SETTINGS.read_text())['uid'])
        except (OSError, ValueError, KeyError):
            pass

    def select(self, uid):
        music({'action':'select', 'uid':str(uid)})
        self.stop()
        self.uid = str(uid)
        SETTINGS.parent.mkdir(exist_ok=True)
        SETTINGS.write_text(json.dumps({'uid':self.uid}))
        self.error = ''

    def stop(self):
        with self._guard:
            self._stop()

    def _stop(self):
        self._generation += 1
        if self.track_id:
            music({'action':'stop','id':self.track_id})
            self.track_id = None

    def play(self, data, loop=False, volume=40):
        with self._guard:
            self._play(data, loop, volume)

    def _play(self, data, loop, volume):
        self.stop()
        if not self.uid:
            raise RuntimeError('请先选择 AirPlay 输出')
        cache=ROOT/'.runtime/airplay_audio';cache.mkdir(parents=True,exist_ok=True)
        path=cache/(hashlib.sha256(data).hexdigest()+'.mp3')
        if not path.exists():path.write_bytes(data)
        # AirPlay/HomePod may resume its own music at natural EOF even with `once`.
        # Keep the tail in a loop and explicitly stop inside padded silence before EOF.
        cutoff = None
        probe = subprocess.run(['/opt/homebrew/bin/ffprobe','-v','error','-show_entries','format=duration',
            '-of','default=noprint_wrappers=1:nokey=1',str(path)],capture_output=True,text=True,check=True)
        cutoff = float(probe.stdout.strip()) + 0.35
        padded = path.with_name(path.stem+'-tail.mp3')
        if not padded.exists():
            subprocess.run(['/opt/homebrew/bin/ffmpeg','-y','-i',str(path),'-af','apad=pad_dur=3',
                '-c:a','libmp3lame','-b:a','64k',str(padded)],capture_output=True,check=True)
        path = padded
        try:
            self.track_id=music({'action':'play','uid':self.uid,'path':str(path),'loop':True,'volume':int(volume)})['id']
            self.error=''
            if cutoff is not None:
                generation=self._generation
                owned=self.track_id
                threading.Thread(target=self._finish_once,args=(generation,owned,cutoff,loop),daemon=True).start()
        except Exception as exc:
            self.error=str(exc)
            raise


    def _finish_once(self, generation, owned, cutoff, loop=False):
        deadline=time.monotonic()+cutoff+20
        while generation==self._generation and (loop or time.monotonic()<deadline):
            try:
                state=music({'action':'state'})
                if generation!=self._generation or state['id']!=owned:
                    return  # User/another app took over: do not stop their track.
                if state['position']>=cutoff:
                    with self._guard:
                        if generation==self._generation:
                            if loop:
                                music({'action':'seek','id':owned,'position':0})
                            else:
                                self.stop()
                                return
            except Exception as exc:
                self.error=str(exc)
            time.sleep(.25)
        if generation==self._generation:
            self.stop()


AIRPLAY = AirPlayPlayer()
