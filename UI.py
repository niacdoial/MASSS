#!/bin/false

# this file takes care of the UI definition for the MASSS

import sys
import os
import os.path
import re
import tkinter as tk
import tkinter.ttk as ttk
import asyncio as aio
from math import floor, ceil

skip_detector = re.compile(r'(?P<core>.*?)\#'+
                           r'(?P<seconds>[0-9]*)'+
                           r'\.(?P<ext>[a-zA-Z0-9]*)')

# This class create a gtk.ToggleButton linked to a sound file.
# Send "toggle", it plays the sound. Send "toggle" again, it stops.
class SndButton(tk.Frame):   # ## a button associated to a file. sends playback commands
    def __init__(self, master, fullname, interface, parent_scrollableframe):
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

        self.vol = tk.Scale(self, from_=0, to=2, orient=tk.HORIZONTAL, resolution=0.05, length=150, command=self.onUpdate)
        self.vol.set(1)
        self.vol.pack(side=tk.LEFT)

        self.btn.pack(side=tk.LEFT, fill=tk.X)

        # ensure correct scrolling for parent...
        for binded in (self, self.btn, self.vol):
            binded.bind("<MouseWheel>", parent_scrollableframe.onMousewheel)
            binded.bind("<Button-4>", parent_scrollableframe.onMousewheel)
            binded.bind("<Button-5>", parent_scrollableframe.onMousewheel)



    def onPress(self, event=None):
        if self.state.get()==1:  # at this point, the state is already changed!
            # this will have to be done in two parts:
            # the polling and the callback (because play() is a coroutine)
            # callback is another method
            playtask = self.interface.loop.create_task(
                self.interface.play(self.filename, self.skip, self.onStop)
            )
            playtask.add_done_callback(self._onPress_activate_part2)
        else:
            if self.instance_id is None:
                # it didn't start playing completely
                print("warning: tried to stop track before full activation")
            inst_id = self.instance_id
            self.instance_id = None
            self.interface.stop(inst_id)

    def _onPress_activate_part2(self, future):
        """the callback for when the VLC instance started playing"""
        result = future.result()
        self.instance_id = result
        self.onUpdate()


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

        self.inner.bind("<MouseWheel>", self.onMousewheel)
        self.inner.bind("<Button-4>", self.onMousewheel)
        self.inner.bind("<Button-5>", self.onMousewheel)

        # subdir (and subfile) configuration
        subdirs = os.listdir(directory)
        subfiles = []
        i=0
        for i, subdir in enumerate(subdirs):
            is_dir = os.path.isdir(os.path.join(directory, subdir))
            if is_dir and not is_root:
                frame = tk.LabelFrame(self.inframe, text=subdir, labelanchor='n')
                #frame.columnconfigure(0, weight=1)

                # manually bind children too to ensure correct scrolling...
                frame.bind("<MouseWheel>", self.onMousewheel)
                frame.bind("<Button-4>", self.onMousewheel)
                frame.bind("<Button-5>", self.onMousewheel)

                files = os.listdir(os.path.join(directory, subdir))

                for j, file in enumerate(files):
                    button = SndButton(frame, os.path.join(directory, subdir, file), interface, self)
                    button.pack(fill=tk.X)
                frame.grid(column=0,row=i, sticky='W')
            elif not is_dir:  # check that only files end up here
                subfiles.append(subdir)

        if subfiles:
            frame = tk.LabelFrame(self.inframe, text='DIR_ROOT', labelanchor='n')
            for j, file in enumerate(subfiles):
                button = SndButton(frame, os.path.join(directory, file), interface)
                button.pack(fill=tk.X)
            frame.grid(column=0,row=i+1, align='W')

    def onInnerConfigure(self, event):
        '''Reset the scroll region to encompass the inner frame'''
        self.inner.configure(scrollregion=self.inner.bbox("all"))
        # change canvas width. somehow only height is done automatically
        self.inframe.update_idletasks()
        self.inner.configure(width=self.inframe.winfo_width())
        self.configure(width=self.inner.winfo_width()+self.bar.winfo_width())

    def onMousewheel(self, event):
        if event.delta != 0:
            if event.delta < 0:
                delta = floor(event.delta/120)
            else:
                delta = ceil(event.delta/120)
        elif event.num in (4,5):
            delta = int(2*(4.5-event.num))
        else:
            print('scrolling unimplemented here')
            return
        self.inner.yview("scroll", delta, "units")
        return "break"

class EqFrame(tk.LabelFrame):
    """frame with the equaliser bars. knows when to call the VlcInterface to adjust its equalizer."""
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
            #bar.bind('<ButtonPress-1>', self.interface.deactivate)
            #bar.bind('<ButtonRelease-1>', self.interface.reactivate)
            bar.bind('<ButtonRelease-1>', self.sendUpdate)

    def getstr(self):
        ret = ' '
        for x in self.bars:
            ret += (str(x.get()) + ' ')
        return ret

    def onUpdate(self, event=None):
        return
        self.sendUpdate()
    def sendUpdate(self, event=None):
        print("EqFrame-sendUpdate")
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


def create_ui(vlc_interface):
    win = tk.Tk()
    win.rowconfigure(0, weight=1)  # UI stratching configuration...
    win.columnconfigure(0, minsize=100, weight=0)
    win.columnconfigure(1, weight=100)
    win.columnconfigure(2, weight=1)

    a = MainFileChooser(win, vlc_interface, './sounds')
    a.grid(row=0, column=0, sticky='wnes', rowspan=2)

    b = EqFrame(win, vlc_interface)
    b.grid(row=0, column=1, sticky='news', rowspan=2)

    v = VolFrame(win, vlc_interface)
    v.grid(row=0, column=2, sticky='nes')

    s = OverrideFrame(win, vlc_interface)
    s.grid(row=1, column=2, sticky='es')
    if sys.platform!='linux':
        # this is windows. add the 'foreground' option
        win.wm_attributes("-topmost", 1)

    win.bind('<Destroy>', vlc_interface.startFinalization)

    return win
