#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) ???
# Simple Sampler plays a sound when you push its button.

import tkinter as tk
import tkinter.ttk as ttk
import sys
import os
import os.path
from time import time, sleep
from socket import socket
import subprocess as sb
from select import select
import re

#### credits
# original ASSS by Hugh Tebby ( (c) 2010 )
# lazy-ass scrollable frame from https://stackoverflow.com/questions/40526496/vertical-scrollbar-for-frame-in-tkinter-python

#### TODO
# weird misbehave when changing everything in a row ("speedrun glitch") (is it fixed now?)
# fix the misbahaving skipping functionnality
# real time vlc crash detection?

# ### constants
skip_detector = re.compile(r'(?P<core>.*?)\#'+
                           r'(?P<seconds>[0-9]*)'+
                           r'\.(?P<ext>[a-zA-Z0-9]*)')
port_start = 8990
port_end = 9089

# ### a helper function
def port_increment(port):
    if port != port_end:  # cycle between 8990 and 9089 ports
        return port + 1
    else:
        return port_start

def get_vlc_prgrm():
    
    if sys.platform=='linux':
        return ('vlc', None)
    else:  # assume windows
        file = open('.\\vlc.path', 'r')
        vlcpath = file.readline()[:-1]
        wd = os.path.abspath(file.readline()[:-1])+'\\'
        file.close()
        return vlcpath, wd

# ### file playback control classes

# more like an evolved struct: attributes will be accessed by VlcInterface
class VlcInstance:
    def __init__(self, port, eq_cache, vol_cache, tk_master):
        self.tk_master = tk_master  # used for after() to guess when the track ends
        self.stop_token = None
        self.is_dirty = False  # used extarnally
        self.is_playing = False
        self.vol_modifier = 1.0
        self.vol_cache = vol_cache
        self.term_attempts = 0  # used for cleaning
        self.term_time = 0
        
        program, cwd = get_vlc_prgrm()
        print(cwd)
        arg1 = ['--audio-filter="equalizer"', '--equalizer-bands="{:s}"'.format(eq_cache)]
        
        for i in range(99):
            arg2 = ["-I rc", "--rc-host=\" 127.0.0.1:{:d} \"".format(port)]
            # arg2 = ["-I cli", "--lua-config=\"cli={{host='localhost:{:d}'}}\"".format(port)]

            cmd = [program] + arg1 + arg2
            print(cmd)
            self.vlc = sb.Popen(cmd, stdout=sb.DEVNULL, stderr = sb.STDOUT, shell=False)
            try:
                print('trying to start vlc at port', port, '...')
                # self.vlc.stdout.readlines(2)  # will wait for vlc to initialize
                # second line says 'listening' (fail)
                i = 0  # wait at most two seconds (for very slow machines)
                sleep(0.08)
                while True: 
                    try:
                        self.sock = socket()
                        self.sock.connect(('127.0.0.1', port))
                        break
                    except:
                        print('waiting ', i)
                        if i > 20:
                            raise
                        sleep(0.1)
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
            
    def terminate(self):
        self.sock.send(b'shutdown\n')
        self.sock.close()
        self.term_time = time()
        self.term_attempts = 1
    
    def is_cleanable(self, event=None):
        r_flag, _ , exc_flag = select([self.sock], [], [self.sock], 0)
        if r_flag:  # something to read in socket
            print(self.sock.recv(256).decode()
                  .replace('\r\n', '\n').replace('\n> ','').replace('> ','').strip())
            # print output (without the input prompts)
        if exc_flag:
            self.terminate()
            return True
        if (not self.is_playing) and (self.is_dirty):
            self.terminate()
            return True
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
    def play(self, filename, skip, on_stop):
        if self.is_playing:
            self.stop()
        self.sock.send('add {:s}\n'.format(filename).encode())
        if skip is not None:
            sleep(0.1)
            self.sock.send('seek {:s}\n'.format(skip).encode())
        self.on_stop = on_stop
        self.is_playing=True
        sleep(0.02)
        while select([self.sock],[],[],0)[0]:
            self.sock.recv(512)  # this won't block because each call to vlc generates output
        sleep(0.8)    
        self.sock.send(b'get_length\n')
        # response should be 't\n> ' where t is in seconds
        sleep(0.1)
        response = self.sock.recv(256)
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
        self.stop_token = self.tk_master.after(length*1000 + 500, self.stop)
        
    def stop(self, event=None):
        self.sock.send(b'stop\n')
        
        if self.stop_token is not None:  # hope after_cancel on an ongoing callback's id doesn't break anything
            self.tk_master.after_cancel(self.stop_token)
            self.stop_token = None
        else:
            print("warning: a button won't uncheck itself automatically at track end")
        self.on_stop()
        self.on_stop = None
        self.is_playing = False
        
class VlcInterface:  # a proper communicaiton interface with vlc. manages all the commands
    def __init__(self, window):
        # variable initialization
        self.window = window
        
        self.on_stop = None  # cache
        self.eq_cache = ' ' + 10* '0 '
        self.skip_override = None
        self.vol_cache=0.5
        
        self.port = 8990
        
        self.instances = []
        self.old_instances = []
        self.broken_instances = []
        self.cleaningtask = None
        
    def add_instance(self):
        inst = VlcInstance(self.port, self.eq_cache, self.vol_cache, self.window)
        self.port = inst.port +1
        self.instances.append(inst)
        
    def vol(self, v=None):
        if v is not None:
            self.vol_cache = v
        for inst in self.instances:
            inst.vol(self.vol_cache)
    def set_vol_mod(self, id, v):
        self.instances[id].vol_modifier = v
        self.instances[id].vol()
    def eq(self, str):
        self.eq_cache = str
        for inst in self.instances:
            inst.is_dirty = True
            
    def play(self, filename, skip=None, on_stop=(lambda:None)):            
        if self.skip_override is not None:
            skip = self.skip_override
        i=0
        while i<len(self.instances):
            if self.instances[i] is not None and \
                    not self.instances[i].is_playing:
                try:
                    self.instances[i].play(filename, skip, on_stop)
                    break
                except OSError:  # broken pipe. assume dead
                    self.broken_instances.append(self.instances[i])
                    self.instances[i] = None
                    i += 1
                    
            else:
                i += 1
        else:  # no free instance: SHOULDN'T HAPPEN, but take care of it here.
            self.add_instance()
            i=len(self.instances) -1
            self.instances[i].play(filename, skip, on_stop)
        
        if i+1 ==len(self.instances):
            # last instance used. need to ready one more:
            self.add_instance()
        return i

    def stop(self, id):
        try:
            self.instances[id].stop()
        except OSError:  # something crashed... not tat it matters right now
            self.broken_instances.append(self.instances[id])
            self.instances[id] = None
        
    def clean(self, event=None):
        # check for dirty instances that have to be renewed
        for i in range(len(self.instances)):
            try:
                if self.instances[i] and self.instances[i].is_cleanable():
                    self.old_instances.append(self.instances[i])
                    self.instances[i] = None
            except OSError:
                # assume this means the instance is not None, and died.
                self.broken_instances.append(self.instances[i])
                self.instances[i] = None
        # check for broken instances and deal with them
        for inst in self.broken_instances:
            inst.terminate_broken()
        self.old_instances += self.broken_instances
        self.broken_instances = []
        
        # see if anything can be deleted
        self.clean_instances()
        
        # clean the instance list, because when instances are removed from list, they are raplaced with None
        for i in range(len(self.instances)-1, -1, -1):
            if self.instances[i] is None:
                del self.instances[i]
            else:
                break  # only clean the end of the list
        
        # ensure at least one available instance
        if not self.instances:  # empty
            self.add_instance()
        
        self.cleaningtask = self.window.after(500, self.clean)
        # cleaning every 0.5s
        
    def clean_instances(self):
        i=0
        while i<len(self.old_instances):
            if self.old_instances[i].check_termination():
                del self.old_instances[i]
            else:
                i+=1

    def deactivate(self, event=None):
        self.window.after_cancel(self.cleaningtask)
    def reactivate(self, event=None):
        self.cleaningtask = self.window.after(500, self.clean)
        
    def onQuit(self):
        self.window.after_cancel(self.cleaningtask)  # stop cleaning schedule
        while len(self.instances):
            if self.instances[0] is not None:
                self.instances[0].terminate()
                self.old_instances.append(self.instances[0])
            del self.instances[0]
        
        sleep(0.05)
        self.clean_instances()  # check all instances closed correctly
        if not self.old_instances:
            return
        sleep(1.5)
        self.clean_instances()  # insist
        if not self.old_instances:
            return
        sleep(2)
        self.clean_instances()  # force termination
        if not self.old_instances:
            return
        sleep(2)
        self.clean_instances()  # I SAID FORCE TERMINATION
 
# ### UI classes 

# This class create a gtk.ToggleButton linked to a sound file.
# Send "toggle", it plays the sound. Send "toggle" again, it stops.
class SndButton(tk.Frame):   # ## a button associated to a file. sends playback commands
    def __init__(self, master, fullname, interface):
        tk.Frame.__init__(self, master)
        
        self.filename = fullname
        self.name = os.path.split(fullname)[1]
        self.interface = interface
        self.vol_modifier = 1.0
        self.instance_id = None
        has_skip = skip_detector.fullmatch(self.name)
        
        if has_skip:
            self.skip = has_skip.groupdict()['seconds'] [:-1]
            # the [:-1] is because tenths of seconds cannot be skipped yet.
        else:
            self.skip = None
      
        self.state = tk.IntVar(master)
        self.btn = tk.Checkbutton(self, text=self.name, 
                                           command=self.onPress, indicatoron=0, variable=self.state, compound=tk.BOTTOM)
        self.btn.pack(side=tk.LEFT, fill=tk.X)
        
        self.vol = tk.Scale(self, from_=0, to=2, orient=tk.HORIZONTAL, resolution=0.05, length=150, command=self.onUpdate)
        self.vol.set(1)
        self.vol.pack(fill=tk.X)
        
        
    
    def onPress(self, event=None):
        if self.state.get()==1:  # at this point, the state is already changed!
            self.instance_id = self.interface.play(self.filename, self.skip, self.onStop)
            self.onUpdate()
        else:
            inst_id = self.instance_id
            self.instance_id = None
            self.interface.stop(inst_id)
    def onUpdate(self, event=None):
        if self.instance_id is not None:
            self.interface.set_vol_mod(self.instance_id, self.vol.get())
    
    def onStop(self):
        self.state.set(0)
        self.btn.deselect()

class MainFileChooser(ttk.Notebook):  # the main panel to load audio files
    def __init__(self, master, interface, directory):
        ttk.Notebook.__init__(self, master)
                   
        dirs = os.listdir(directory)
        
        subfiles=[]
        for subdir in dirs:
            fullname = os.path.join(directory, subdir)
            if os.path.isdir(fullname):
                frame = FileChooserFrame(self, interface, fullname)
                self.add(frame, text=subdir)
            else:
                subfiles.append(fullname)
        if subfiles:
            frame = FileChooserFrame(self, interface, directory, is_root=True)
            self.add(frame, text='SOUNDS_ROOT')


class FileChooserFrame(tk.Frame):  # one of the 'tabs' of the file panel
    def __init__(self, master, interface, directory, is_root=False):
        # ## basic UI configuration (heavier because we need a scrollable frame)
        tk.Frame.__init__(self, master)
        
        self.inner = tk.Canvas(self)
        
        self.inframe = tk.Frame(self.inner)
        
        self.bar = tk.Scrollbar(self, command=self.inner.yview)
        self.bar.pack(side = tk.RIGHT, fill = tk.Y)
        self.inner.configure(yscrollcommand = self.bar.set)
        self.inframe_id = self.inner.create_window((0,0), window=self.inframe, anchor="nw")
        self.inframe.bind('<Configure>', self.onInnerConfigure)
        self.inner.pack(fill=tk.BOTH, expand=1)
        
        # subdir (and subfile) configuration
        subdirs = os.listdir(directory)
        subfiles = []
        for i, subdir in enumerate(subdirs):
            is_dir = os.path.isdir(os.path.join(directory, subdir))
            if is_dir and not is_root:
                frame = tk.LabelFrame(self.inframe, text=subdir, labelanchor='n')
            #frame.columnconfigure(0, weight=1)
            
                files = os.listdir(os.path.join(directory, subdir))
            
                for j, file in enumerate(files):
                    button = SndButton(frame, os.path.join(directory, subdir, file), interface)
                    button.pack(fill=tk.X)
                frame.pack()
            elif not is_dir:  # check that only files end up here
                subfiles.append(subdir)
                
        if subfiles:
            frame = tk.LabelFrame(self.inframe, text='DIR_ROOT', labelanchor='n')
            for j, file in enumerate(subfiles):
                button = SndButton(frame, os.path.join(directory, file), interface)
                button.pack(fill=tk.X)
            frame.pack()
    
    def onInnerConfigure(self, event):
        '''Reset the scroll region to encompass the inner frame'''
        self.inner.configure(scrollregion=self.inner.bbox("all"))
        # change canvas width. somehow only height is done automatically
        self.inframe.update_idletasks()
        self.inner.configure(width=self.inframe.winfo_width())
        self.configure(width=self.inner.winfo_width()+self.bar.winfo_width())

class EqFrame(tk.LabelFrame):
    def __init__(self, master, interface):
        tk.LabelFrame.__init__(self, master, text='Equalizer (values in dB)', labelanchor = 'n')
                   
        self.rowconfigure(1, weight=1)  # UI stretching configuration...
        for i in range(10):
            self.columnconfigure(i, weight=1)
        self.master = master
        self.interface = interface
        
        self.bars = [tk.Scale(self, from_=20, to=-20, label = str(i+1), command=self.onUpdate)
                            for i in range(10)]
        
        for i, bar in enumerate(self.bars):
            bar.grid(row=1, column = i, sticky='news')
            bar.bind('<ButtonPress-1>', self.interface.deactivate)
            bar.bind('<ButtonRelease-1>', self.interface.reactivate)
    
    def getstr(self):
        ret = ' '
        for x in self.bars:
            ret += (str(x.get()) + ' ')
        return ret
    
    def onUpdate(self, event=None):
        self.interface.eq(self.getstr())
    
class VolFrame(tk.LabelFrame):   # ## a widget to control volume
    def __init__(self, master, interface):
        tk.LabelFrame.__init__(self, master, text='Master\nvolume\n(0-125%)', labelanchor = 'n')
                   
        self.rowconfigure(1, weight=1)  # UI stretching configuration...
        self.columnconfigure(0, weight=1)
        self.master = master
        self.interface = interface
        

        self.bar = tk.Scale(self, from_=125, to=0, command=self.onUpdate)
        self.bar.set(50)
        
        self.bar.grid(row=1, column = 0, sticky='news')
        
    def onUpdate(self, event=None):
        self.interface.vol(self.bar.get()/100)
        
class OverrideFrame(tk.LabelFrame):  # for the volume override
    def __init__(self, master, interface):
        self.interface = interface
        tk.LabelFrame.__init__(self, master, text='skipping override (seconds)')
        
        self.enable = tk.BooleanVar()
        self.check = tk.Checkbutton(self, text='enable', command=self.onPress,
                                    variable=self.enable)
        self.entry = tk.Entry(self, width=6)
        self.entry.bind('<Return>', self.onPress)
        self.entry.bind('<KP_Enter>', self.onPress)
        self.check.pack(side=tk.TOP)
        self.entry.pack()
        
    def onPress(self, event=None):
        if self.enable.get():
            if re.fullmatch('[0-9]+', self.entry.get()):
                self.interface.skip_override = self.entry.get()
            else:
                print('skip override must be an int')
            
        else:
            self.interface.skip_override = None

# ### main program
def main():
    win = tk.Tk()
    win.rowconfigure(0, weight=1)  # UI stratching configuration...
    win.columnconfigure(0, minsize=100, weight=0)
    win.columnconfigure(1, weight=100)
    win.columnconfigure(2, weight=1)
    
    inter =VlcInterface(win)
    inter.vol(0.5)  
    inter.cleaningtask = win.after(500, inter.clean)  # schedule cleaning every half second
    
    a = MainFileChooser(win, inter, './sounds')
    a.grid(row=0, column=0, sticky='wnes', rowspan=2)
    
    b = EqFrame(win, inter)
    b.grid(row=0, column=1, sticky='news', rowspan=2)
    
    v = VolFrame(win, inter)
    v.grid(row=0, column=2, sticky='nes')
    
    s = OverrideFrame(win, inter)
    s.grid(row=1, column=2, sticky='es')
    if sys.platform!='linux':
        # this s windows. add the 'foreground' option
        win.wm_attributes("-topmost", 1)
    try:
        win.mainloop()
    except Exception as err:
        # should happen when the window is closed
        pass
    finally:
        inter.onQuit()

if __name__ == "__main__":
    main()
