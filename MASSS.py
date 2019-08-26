#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) ???
# Simple Sampler plays a sound when you push its button.

# main file: contains the main functions as well as the VLC instance management code


import sys
import os.path
from time import monotonic as time
import UI
import asyncio as aio
from instances import VlcInstance, port_increment

#### credits
# original ASSS from Hugh Tebby's github  ( (c) 2010 )
# scrollable frame copied from from https://stackoverflow.com/questions/40526496/vertical-scrollbar-for-frame-in-tkinter-python

#### TODO
# weird misbehave when changing everything in a row ("speedrun glitch") (is it fixed now?)
# real time vlc crash detection?
# make a more coherent (and understandable) command line output


# ### file playback control classes

class VlcInterface:  # a proper communicaiton interface with vlc. manages all the commands
    def __init__(self):
        # variable initialization

        # event loop stuff
        self.loop = aio.get_event_loop()
        self.is_terminated = False

        # cache for instance control
        self.eq_cache = ' ' + 10* '0 '
        self.skip_override = None
        self.vol_cache=0.5
        self.port = 8990

        # instance managment and cleaning
        self.instances = []
        self.old_instances = []
        self.broken_instances = []
        self.loading_instances = []
        self.cleaningtasks = []

    # ## instance control methods

    def vol(self, v=None):
        """sets the VlcInstances's volumes according to the master volume `v`"""
        if v is not None:
            self.vol_cache = v
        for inst in self.instances:
            if inst is not None:
                inst.vol(self.vol_cache)

    def set_vol_mod(self, id, v):
        """sets the volume modifier of a VlcInstance, and updates its real volume accordingly"""
        self.instances[id].vol_modifier = v
        self.instances[id].vol()

    def eq(self, str):
        """changes the global equalizer. Note: this will only take effect on new songs, and the VLcInstances need to be rebooted"""
        self.eq_cache = str
        for inst in self.instances:
            inst.is_dirty = True
        self.add_task(self.clean__comb())

    async def play(self, filename, skip=None, on_stop=(lambda:None)):
        """makes one track play. selects the right VlcInstance for that."""
        if self.skip_override is not None:
            skip = self.skip_override
        i=0
        while i<len(self.instances):
            if self.instances[i] is not None and \
                    not self.instances[i].is_playing:
                try:
                    await self.instances[i].play(filename, skip, on_stop)
                    break
                except OSError:  # broken pipe. assume dead VLC instance.
                    self.broken_instances.append(self.instances[i])
                    self.add_task(self.clean__comb())
                    self.instances[i] = None
                    i += 1
            else:  # if it is an occupied/voided instance
                i += 1
        else:  # no free instance: SHOULDN'T HAPPEN, but take care of it here.
            i = await self.clean__check_initialized()
            if i is None:
                i = await self.add_instance(True)
            await self.instances[i].play(filename, skip, on_stop)

        for j in range(i, len(self.instances)):
            if self.instances[j] is not None and \
                    not self.instances[j].is_playing:
                break
        else:  # no break happened: no other free instance: create one ASAP.
            self.add_task(self.clean__refill())


        return i

    def stop(self, id):
        print('stop inst', id)
        try:
            self.instances[id].stop()
            if self.instances[id].is_dirty:
                self.add_task(self.clean__comb())
        except OSError:  # something crashed... not tat it matters right now
            self.broken_instances.append(self.instances[id])
            self.instances[id].on_close()  # finalize stuff in the instance's stead
            self.instances[id] = None
            self.add_task(self.clean__comb())

    # ## instance management and cleaning methods

    def add_task(self, awaitable):
        self.cleaningtasks.append(self.loop.create_task(awaitable))

    async def add_instance(self, immediate_instance=False):
        # three uses for immediate_instance:
        # False, True, and the initialized VlcInstance to be added
        if isinstance(immediate_instance, bool):
            # the forst two need the creation of an instance
            inst = VlcInstance(self.port, self.eq_cache, self.vol_cache,
                               self.loop)
        else:
            inst = immediate_instance
        if not immediate_instance:
            # for False only
            self.loading_instances.append(inst)
        else:
            await inst.ensure_started()  # wait for the instance to fully start
            self.port = port_increment(inst.port)
            for i, otherinst in enumerate(self.instances):
                if otherinst is None:
                    self.instances[i] = inst
                    return i
            else:
                self.instances.append(inst)
                return(len(self.instances)-1)

        # queue a cleaning operation for when this instance will (hopefully) be ready.
        self.add_task(self.clean__check_initialized(0.2))

    async def clean__check_initialized(self, after=0):
        """goes through all the instances being initialized, and takes care of the ones which finished."""
        """return the first which finished if it exists"""

        # this method can arrange for a later call of itself.
        # but it needs to be able to wait for this.
        await aio.sleep(after)

        i = 0
        first_new_instance_id = None
        while i< len(self.loading_instances):
            inst = self.loading_instances[i]
            if inst.start_task is None:  # if it completed its starting coroutine
                temp_id = await self.add_instance(inst)
                if first_new_instance_id is None:
                    first_new_instance_id = temp_id
                self.port = port_increment(inst.port)
                del self.loading_instances[i]
            else:
                i += 1

        # queue a re-run if necessary
        if len(self.loading_instances) > 0:
            self.add_task(self.clean__check_initialized(0.2))

        return first_new_instance_id


    async def clean__comb(self, event=None):
        """removes instances that should be cleaned from the main list"""
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

        # clean the instance list, because when instances are removed from list, they are raplaced with None
        for i in range(len(self.instances)-1, -1, -1):
            if self.instances[i] is None:
                del self.instances[i]
            else:
                break  # only clean the end of the list

        # see if anything can be deleted, and if we need more instances
        self.add_task(self.clean__refill())
        self.add_task(self.clean__terminate_old())

    async def clean__refill(self, event=None):
        """create new instances if necessary"""

        await self.clean__check_initialized()

        # ensure at least one available instance
        for j in range(len(self.instances)):
            if self.instances[j] is not None and \
                    not self.instances[j].is_playing:
                break
        else:  # no break happened
            await self.add_instance()


        #self.cleaningtask = self.window.after(500, self.clean)
        # cleaning every 0.5s

    async def clean__terminate_old(self):
        """tries to terminate the abandonned instances (broken or obselete)"""
        i=0
        while i<len(self.old_instances):
            if self.old_instances[i].check_termination():
                del self.old_instances[i]
            else:
                i+=1

    def clean__remove_tasks(self):
        i=0
        while i < len(self.cleaningtasks):
            if self.cleaningtasks[i].done() or self.cleaningtasks[i].cancelled():
                del self.cleaningtasks[i]
            i += 1

    # ## quitting methods...
    async def onQuit(self):
        # first, be sure that all the tasks are done
        self.clean__remove_tasks()  # this one is syncronous!
        for task in self.cleaningtasks:
            await task

        while len(self.instances):
            if self.instances[0] is not None:
                self.instances[0].terminate()
                self.old_instances.append(self.instances[0])
            del self.instances[0]

        await aio.sleep(0.05)
        await self.clean__terminate_old()  # check all instances closed correctly
        if not self.old_instances:
            return
        await aio.sleep(1.5)
        await self.clean__terminate_old()  # insist
        if not self.old_instances:
            return
        await aio.sleep(2)
        await self.clean__terminate_old()  # force termination
        if not self.old_instances:
            return
        await aio.sleep(2)
        await self.clean__terminate_old()  # I SAID FORCE TERMINATION

    def startFinalization(self, event=None):
        # for some reason, this function can be called a LOT of times.
        # ths is an ugly patch, but it works
        if self.is_terminated:
            print("stop kicking dead horses", end="   ")
            return
        print("starting VlcInterface termination")
        self.is_terminated = True
        # the finalisation should be done cleanly, without sending tasks around.
        # the tasks that are launched are at most 0.2 seconds long as long as they don't requeue themselves

        # ensure thet they don't requeue
        self.instances += self.loading_instances
        self.loading_instances = []

        # now create the final onQuit task, which will wait for the termination of the other tasks
        self.termination_task = self.loop.create_task(self.onQuit())




# ### main program

async def mainloop(win, inter):
    # create the base instance:
    await inter.clean__refill()
    try:
        while not inter.is_terminated:
            beg = time()
            inter.clean__remove_tasks()
            win.update()
            remaining = beg + 1/60 - time()
            if remaining > 0:
                await aio.sleep(remaining)
    finally:
        if not inter.is_terminated:
           inter.startFinalization()
        await inter.termination_task
        try:
            # avoid recursion in termination calls!
            win.unbind('<Destroy>')
            win.destroy()
        except Exception:
            pass

def main():
    inter = VlcInterface()
    inter.vol(0.5)
    #inter.cleaningtask = win.after(500, inter.clean)  # schedule cleaning every half second

    win = UI.create_ui(inter)
    #inter.loop.set_exception_handler(exc_handl)
    inter.loop.run_until_complete(mainloop(win, inter))

if __name__ == "__main__":
    main()
