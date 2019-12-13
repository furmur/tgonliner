#!/usr/bin/python3
# -*- coding: utf8 -*-

import asyncio
import random
import configparser
import os, sys
import signal
import time
import re
import platform
import subprocess
import copy

from enum import Enum
from telethon import TelegramClient, sync, events, functions, types
from telethon.tl.types import PeerUser, PeerChat, PeerChannel

SIGHUP_AVAILABLE = hasattr(signal, 'SIGHUP')

KEEP_ONLINE_INTERVAL_SECONDS = 30
#~ KEEP_ONLINE_INTERVAL_SECONDS = 5

def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S") + ' ' + msg)

class Onliner:

    def __init__(self, client):
        self.runtime_version = self.on_version(None,None,True)
        self.enabled = True
        self.client = client
        self.keep_online_timer_task = None

    def start(self):
        self.reset_keep_online_timer()

    def stop(self):
        self.cancel_keep_online_timer()

    def cancel_keep_online_timer(self):
        if self.keep_online_timer_task:
            self.keep_online_timer_task.cancel()
            self.keep_online_timer_task = None
            #~ log('⏳keep online timer is cancelled')

    def reset_keep_online_timer(self):
        self.cancel_keep_online_timer()
        self.keep_online_timer_task = asyncio.create_task(self.timer_loop())
        #~ log('⏳set keep online timer')

    async def timer_loop(self):
        while True:
            try:
                await self.update_status()
            except Exception as e:
                exc_type, exc_obj, exc_tb = sys.exc_info()
                fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                log('⏳%s timer_loop exception %s\n%s %s:%s' % (e,exc_type,fname,exc_tb.tb_lineno))
            await asyncio.sleep(self.watchdog_timeout)

    async def update_status(self):
        await self.client(functions.account.UpdateStatusRequest(offline=False))

    async def handle_incoming_message(self, event):
        #check for possible future usage
        if not self.enabled:
            return None

        #any processing here ?
        return None

    def on_help(self, event, text):
        return '''
s - show status
e - set event processing enabled (%s)
? - this help
v - show version
update - load bot updates
restart - restart bot instance
quit - shutdown bot instance
''' % (self.enabled)

    def on_status(self, event, text):
        return 'enabled: %s' % self.enabled

    def on_enabled(self, event, text):
        self.enabled = not self.enabled
        if self.enabled:
            return 'events processing is enabled'
        else:
            return 'events processing is disabled'

    def on_quit(self, event, text):
        os.kill(os.getpid(), signal.SIGTERM)

    def on_version(self,event, text, startup = False):
        msg = ""

        if not startup:
            msg += "runtime:  \n" + self.runtime_version + "\n"
            msg += "fs:\n"

        msg += "  tags: "
        msg+= subprocess.Popen("git describe --tags", shell=True, stdout=subprocess.PIPE).communicate()[0].decode("utf-8")
        msg += "  commit: "
        msg+= subprocess.Popen("git rev-parse HEAD", shell=True, stdout=subprocess.PIPE).communicate()[0].decode("utf-8")

        return msg

    def on_update(self,event, text):
        msg = ''
        out,err = subprocess.Popen("git pull", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
        if out:
            msg+=out.decode("utf-8")
        if err:
            msg+=err.decode("utf-8")
        return msg

    def on_restart(self,event, text):
        if SIGHUP_AVAILABLE:
            os.kill(os.getpid(), signal.SIGHUP)
        else:
            return "self restart has not available on this platform yet"

    class CtrlCmd:
        def __init__(self, key, handler, exact_match = True):
            self.key = key
            self.handler = handler
            self.exact_match = exact_match

        def match(self, msg):
            if self.exact_match:
                return msg==self.key
            return msg.startswith(self.key)

        def process(self, onliner, event, msg):
            return self.handler(onliner, event, msg)

    control_commands = [
        CtrlCmd('s',on_status),
        CtrlCmd('e',on_enabled),
        CtrlCmd('?',on_help),
        CtrlCmd('quit',on_quit),
        CtrlCmd('update',on_update),
        CtrlCmd('restart',on_restart),
        CtrlCmd('v',on_version)
    ]

    def handle_incoming_control_message(self, event):
        for cmd in self.control_commands:
            if cmd.match(event.raw_text):
                reply = cmd.process(self, event, event.raw_text)
                if reply:
                    return reply
                return
        return self.on_help(event, event.raw_text)

cfg = configparser.ConfigParser()
cfg.read('tgonliner.cfg')

for s in ['api','bot']:
    if not s in cfg:
        raise Exception('missed mandatory section [%s]' % s)
for opt in ['id','hash']:
    if opt not in cfg['api']:
        raise Exception('missed mandatory option "%s" in section [api]' % opt)

client = TelegramClient('tgonliner', cfg['api'].getint('id'), cfg['api']['hash'])
ctl_chat_id = cfg['bot']['ctl_chat_id'] if 'ctl_chat_id' in cfg['bot'] else None

onliner = Onliner(client)

restart = False

def sighup_handler(signum, frame):
    global restart
    restart = True
    log('got SIGHUP. restart instance')
    onliner.stop()
    asyncio.ensure_future(client.disconnect())

def terminate_handler(signum, frame):
    log('terminate instance')
    onliner.stop()
    asyncio.ensure_future(client.disconnect())

if SIGHUP_AVAILABLE:
    signal.signal(signal.SIGHUP, sighup_handler)

signal.signal(signal.SIGINT, terminate_handler)
signal.signal(signal.SIGTERM, terminate_handler)

client.start()

if ctl_chat_id:
    ctl_chat = client.get_entity(PeerChat(int(ctl_chat_id)))

    @client.on(events.NewMessage(outgoing=True, chats=[ctl_chat]))
    async def ctl_handler(event):
        log('👀%s got ctl request: %s' % (event.message.id, event.raw_text))
        try:
            reply = onliner.handle_incoming_control_message(event)
        except Exception as e:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            log('🖕%s exception %s\n%s %s:%s' % (event.message.id,e,exc_type,fname,exc_tb.tb_lineno))
            await client.send_message(ctl_chat_id, '🖕%s exception %s\n%s %s:%s' % (event.message.id,e,exc_type,fname,exc_tb.tb_lineno))

        if(reply):
            await event.respond(reply)

    @client.on(events.NewMessage(incoming=True, chats=[]))
    async def handler(event):
        log('👀%s incoming message' % event.message.id)
    
        try:
            reply = await onliner.handle_incoming_message(event)
        except Exception as e:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            log('🖕%s exception %s\n%s %s:%s' % (event.message.id,e,exc_type,fname,exc_tb.tb_lineno))
            # ~ await client.send_message(ctl_chat_id, '🖕%s exception %s\n%s %s:%s' % (event.message.id,e,exc_type,fname,exc_tb.tb_lineno))

        if reply:
            await onliner.delayed_reply(event,reply)
        # ~ else:
            # ~ log('💤%s no reply generated by onliner' % event.message.id)

    hi_msg = 'started new instance %s with version:\n%s' % (os.getpid(),onliner.runtime_version)
    log(hi_msg)
    client.send_message(ctl_chat, hi_msg)

    print('use ? in ctl chat for help')

else:
    print('ctl_chat_id is not set.\ntype /id in the control chat to get appropriate configuration changes')
    @client.on(events.NewMessage(outgoing=True))
    async def any_handler(event):
        if '/id'==event.raw_text:
            if isinstance(event.message.to_id,PeerChat):
                print('add this option to the [bot] section and restart script:\nctl_chat_id = {}'.format(event.message.to_id.chat_id))

print('entering events processing cycle. use Ctrl+C to terminate or use ctl chat')
onliner.start()
client.run_until_disconnected()

if restart:
    log('replace instance %s' % os.getpid())
    os.execl('/usr/bin/python3','-c',__file__)
else:
    log('bye')
