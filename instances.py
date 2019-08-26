import sys
import os
from time import monotonic as time
from socket import socket
import subprocess as sb
from select import select
import asyncio as aio


# ## helper functions
def get_vlc_prgrm():
    if sys.platform=='linux':
        return ('vlc', None)
    else:  # assume windows
        file = open('.\\vlc.path', 'r')
        vlcpath = file.readline()[:-1]
        wd = os.path.abspath(file.readline()[:-1])+'\\'
        file.close()
        return vlcpath, wd

def get_params(dict):
    res = []
    if sys.platform=='linux' or True:
        for key in dict:
            res.append(key)
            if dict[key]:
                res.append(dict[key])
    else:  # assume windows
        for key in dict:
            temp = key  # string copy
            if dict[key]:
                temp += '='
                if dict[key][0]== dict[key][-1] == '"':
                    temp += dict[key]
                else:
                    temp += '"'+dict[key]+'"'
            res.append(temp)
    return res

def port_increment(port):
    port_start = 8990
    port_end = 9089
    if port != port_end:  # cycle between 8990 and 9089 ports
        return port + 1
    else:
        return port_start

# the vlc instance class itself.
# more like an evolved struct: attributes will be accessed by VlcInterface
class VlcInstance:
    def __init__(self, port, eq_cache, vol_cache, loop):
        self.loop = loop
        self.stop_token = None
        self.is_dirty = False  # used extarnally
        self.is_playing = False
        self.vol_modifier = 1.0
        self.vol_cache = vol_cache
        self.term_attempts = 0  # used for cleaning
        self.term_time = 0

        start_task = self.loop.create_task(self.start(port, eq_cache))
        self.start_task = start_task

    async def ensure_started(self):
        if self.start_task is not None:
            print("warning: a VLC instance is used before its full initialisation.")
            await self.start_task

    async def start(self, port, eq_cache):
        program, cwd = get_vlc_prgrm()
        args = {}
        args['--audio-filter']= "equalizer"
        args["--no-equalizer-2pass"] = None
        args["--equalizer-preamp"] = '12'
        args['--equalizer-bands'] = '"{:s}"'.format(eq_cache)

        for i in range(99):
            args["-I"] = "rc"
            args["--rc-host"]="127.0.0.1:{:d}".format(port)
            # arg2 = ["-I cli", "--lua-config=\"cli={{host='localhost:{:d}'}}\"".format(port)]

            cmd = [program] + get_params(args)
            print(cmd)
            if sys.platform == 'linux':
                self.vlc = sb.Popen(cmd, stdout=sb.PIPE, stderr = sb.STDOUT, shell=False)
            else:
                self.vlc = sb.Popen(cmd, stdout=sb.PIPE, stderr = sb.STDOUT, shell=False, creationflags=sb.SW_HIDE)  # extra windows window management flag
            try:
                # self.vlc.stdout.readlines(2)  # will wait for vlc to initialize
                # second line says 'listening' (fail)
                i = 0  # wait at most two seconds (for very slow machines)
                await aio.sleep(0.08)
                while True:
                    try:
                        self.sock = socket()
                        await self.loop.sock_connect(self.sock, ('127.0.0.1', port))
                        break
                    except:
                        print('waiting ', i)
                        if i > 10:
                            raise
                        await aio.sleep(0.2)
                        i += 1
                self.port = port
                break  # on success
            except Exception as err:
                print('failed.')
                print('|', err)
                self.vlc.kill()
                print('|', self.vlc.stdout.read().decode().replace('\n', '\n |'))
                port = port_increment(port)
        else:  # failed all 99 times (no break):
            raise RuntimeError('failed to connect to all 99 ports.')

        print('vlc started at port', self.port, '(output displayed, but no input is possible here in the console)')
        # set new instance volume
        self.vol()
        self.start_task = None

    def terminate(self):
        if self.start_task:  # this instance is terminated before it could finish...
            self.start_task.cancel()
        try:
            self.sock.send(b'shutdown\n')
            self.sock.close()
        except Exception:
            pass  # if something breaks here, it will be cleaned later on anyway
        self.term_time = time()
        self.term_attempts = 1

    def is_cleanable(self, event=None):
        r_flag, _ , exc_flag = select([self.sock], [], [self.sock], 0)
        if r_flag:  # something to read in socket
            print(self.sock.recv(256).decode()
                  .replace('\r\n', '\n').replace('\n> ','').replace('> ','').strip())
            # print output (without the input prompts)
        if exc_flag:  # if VLC cut the connection (crashed)
            self.terminate_broken()
            return True
        if (self.is_dirty and not self.is_playing):  # if it became obselete
            self.terminate()
            return True
        else:
            return False

    def check_termination(self):
        if self.vlc.poll() is not None:
            return True
        else:
            # semi-gentle timeout 1s
            # hard timeout 3s
            # zombie killer timeout 5s
            if time() > self.term_time +5 and self.term_attempts > 2:
                print("Warning: zombie vlc", self.vlc.pid, 'has to be taken down')
                os.kill(self.vlc.pid, 9)
                return True
            if time() > self.term_time +3 and self.term_attempts > 1:
                print('Warning: vlc instance', self.vlc.pid, 'had to be killed.')
                self.vlc.kill()
                self.term_attempts = 3
                return False
            if time() > self.term_time +1 and self.term_attempts > 0:
                self.vlc.terminate()
                self.term_attempts = 2
                return False

    def terminate_broken(self):
        if self.vlc.poll() is not None:
            # vlc has crashed. Nothing much to do.
            self.sock.close()
        else:
            # pipe was somehow broken without vlc crashing. close it the regular way.
            # (at the next use of check_termination.)
            pass
        self.term_time = time()
        self.term_attempts = 1

    def vol(self, v=None):
        if v is not None:
            self.vol_cache = v
        # None is for updating vlc only
        true_vol = int(self.vol_cache * 256 *self.vol_modifier)
        self.sock.send( 'volume {:d}\n'.format(true_vol) .encode() )

    async def play(self, filename, skip, on_stop):
        if self.is_playing:
            print("warning: for some reason, a track was stopped in order to start a new one. Expect an audio glitch now.")
            self.stop()
        self.sock.send('add {:s}\n'.format(filename).encode())
        if skip is not None:
            await aio.sleep(0.1)
            self.sock.send('seek {:s}\n'.format(skip).encode())
        self.on_stop = on_stop
        self.is_playing=True
        await aio.sleep(0.02)
        while select([self.sock],[],[],0)[0]:
            self.sock.recv(512)  # this won't block because each call to vlc generates output
        #sleep(0.8)
        self.sock.send(b'get_length\n')
        # response should be 't\n> ' where t is in seconds
        response = await self.loop.sock_recv(self.sock, 256)
        for line in response.decode().split('\n'):  # vlc might add logging lines. dodge those.
            try:
                length = int(line)
                if length != 0:
                    break
                else: print('saw a zero for track length.')
            except Exception: pass  # not the right line. next.
        else:  # actually failed...
            # try to be safe.
            print('warning: track length wasn\'t returned correctly. button will not auto-deselect.')
            return

        if skip:
            length -= int(skip)
        # after_func is the window.after tk function
        self.stop_token = self.loop.call_later(length+0.5, self.stop)

    def stop(self, event=None):
        self.sock.send(b'stop\n')

        if self.stop_token is not None:  # hope after_cancel on an ongoing callback's id doesn't break anything
            self.stop_token.cancel()
            self.stop_token = None
        else:
            print("warning: a button won't uncheck itself automatically at track end")
        self.on_stop()
        self.on_stop = None
        self.is_playing = False
