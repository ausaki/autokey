#!/usr/bin/env python

import sys, os, os.path, threading, gamin, string, time, ConfigParser, select, re
import iomediator
from abbreviation import *

CONFIG_FILE = "../../config/abbr.ini"
LOCK_FILE = ".autokey.lck"

# Local configuration sections
CONFIG_SECTION = "config"
DEFAULTS_SECTION = "defaults"
ABBR_SECTION = "abbr"
METHOD_OPTION = "method"

ABBREVIATIONS_LOCK = threading.Lock()
MAX_STACK_LENGTH = 50

def synchronized(lock):
    """
    Synchronisation decorator
    """
    def wrap(f):
        def newFunction(*args, **kwargs):
            lock.acquire()
            try:
                return f(*args, **kwargs)
            finally:
                lock.release()
        return newFunction
    return wrap

def escape_text(text):
    #text = text.replace("\\", "\\\\"
    return text.replace('"','\\\"')  

class ExpansionService:
    
    def __init__(self, trayIcon=None):
        self.trayIcon = trayIcon
        
        # Read configuration
        config = self.__loadAbbreviations()
        self.interfaceType = config.get(CONFIG_SECTION, METHOD_OPTION)
        
        # Set up config file monitoring
        self.monitor = FileMonitor(self.__reloadAbbreviations)
        self.monitor.start()    
    
    def start(self):
        self.mediator = iomediator.IoMediator(self, self.interfaceType)
        self.mediator.start()
        self.inputStack = []
        self.ignoreCount = 0
    
    def pause(self):
        self.mediator.pause()
        
    def is_running(self):
        try:
            return self.mediator.isAlive()
        except AttributeError:
            return False
        
    def switch_method(self, method):
        if self.is_running():
            self.pause()
            restart = True
        else:
            restart = False
        
        self.interfaceType = method
        
        if restart:
            self.start()
            
    def shutdown(self):
        if self.is_running():
            self.pause()
            self.monitor.stop()
        try:
            config = ConfigParser.ConfigParser()
            config.read([CONFIG_FILE])        
            config.set(CONFIG_SECTION, METHOD_OPTION, self.interfaceType)
            fp = open(CONFIG_FILE, 'w')
            config.write(fp)
        except Exception:
            pass
        finally:
            fp.close()
    
    def handle_keypress(self, key):        
       
        if key == iomediator.KEY_BACKSPACE:
            # handle backspace by dropping the last saved character
            self.inputStack = self.inputStack[:-1]
            
        elif key is None:
            self.inputStack = []
        
        elif len(key) > 1:
            self.inputStack = []

        else:
            # Key is a character
                self.inputStack.append(key)
                abbreviations = self.__getAbbreviations()

                for abbreviation in abbreviations:
                    expansion = abbreviation.check_input(self.inputStack)
                    if expansion is not None: break
                
                if expansion is not None:
                    self.mediator.send_backspace(expansion.backspaces)
                    
                    # Shell expansion
                    text = os.popen('/bin/echo -e "%s"' % escape_text(expansion.string)).read()
                    text = text[:-1] # remove trailing newline
                    
                    self.mediator.send_string(text)
                    
                    self.mediator.send_up(expansion.ups)
                    self.mediator.send_left(expansion.lefts)
                    
                    #self.ignoreCount = len(text)
                    self.mediator.flush()
                    
                
        if len(self.inputStack) > MAX_STACK_LENGTH: 
            self.inputStack.pop(0)
            
        #print self.inputStack
    
    @synchronized(ABBREVIATIONS_LOCK)
    def __getAbbreviations(self):
        """
        Synchronised access to the abbreviations is required due to prevent simultaneous
        access by the monitoring thread and the expansion thread.
        """
        return self.abbreviations
    
    @synchronized(ABBREVIATIONS_LOCK)
    def __setAbbreviations(self, abbr):
        """
        @see: __getAbbreviations
        """
        self.abbreviations = abbr
        
    def __loadAbbreviations(self):
        p = ConfigParser.ConfigParser()
        p.read([CONFIG_FILE])
        abbrDefinitions = dict(p.items(ABBR_SECTION))
        defaultSettings = dict(p.items(DEFAULTS_SECTION))
        applySettings(Abbreviation.global_settings, defaultSettings)
        abbreviations = []
        
        for definition in abbrDefinitions.keys():
            # Determine if definition is an option
            if '.' in definition:
                if definition.split('.')[1] in ABBREVIATION_OPTIONS:
                    continue # skip this definition
            
            abbreviations.append(Abbreviation(definition, abbrDefinitions))
            
        self.__setAbbreviations(abbreviations)
        
        return p
            
    def __reloadAbbreviations(self):
        try:
            self.__loadAbbreviations()
            if self.trayIcon is not None:
                self.trayIcon.config_reloaded()
        except Exception, e:
            self.trayIcon.config_reloaded("Abbreviations have not been reloaded.\n" + str(e))
        
class FileMonitor(threading.Thread):
    
    def __init__(self, closure):
        threading.Thread.__init__(self)
        self.closure = closure
        self.monitor = WatchMonitorWrapper()
        self.event = threading.Event()
        self.setDaemon(True)
        
    def run(self):
        self.monitor.watch_file(CONFIG_FILE, lambda x, y: True)
        time.sleep(0.5)
        self.monitor.handle_events()
        
        while not self.event.isSet():
            readReady, writeReady, err = select.select([self.monitor], [], [], 5.0)
            if self.monitor in readReady:
                self.closure()
                self.monitor.handle_events()
        
        self.monitor.stop_watch(CONFIG_FILE)    
        
    def stop(self):
        self.event.set()

class WatchMonitorWrapper(gamin.WatchMonitor):
    
    def fileno(self):
        return self.get_fd()