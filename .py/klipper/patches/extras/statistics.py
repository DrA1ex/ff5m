# Support for logging periodic statistics
#
# Copyright (C) 2018-2021  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, time, logging

class PrinterSysStats:
    def __init__(self, config):
        printer = config.get_printer()
        self.last_process_time = self.total_process_time = 0.
        self.last_load_avg = 0.
        self.last_mem_avail = 0
        self.last_mem_free_cache = None
        self.mem_file = None
        try:
            self.mem_file = open("/proc/meminfo", "r")
        except:
            pass
        printer.register_event_handler("klippy:disconnect", self._disconnect)
    def _disconnect(self):
        if self.mem_file is not None:
            self.mem_file.close()
            self.mem_file = None
    def stats(self, eventtime):
        # Get core usage stats
        ptime = time.process_time()
        pdiff = ptime - self.last_process_time
        self.last_process_time = ptime
        if pdiff > 0.:
            self.total_process_time += pdiff
        self.last_load_avg = os.getloadavg()[0]
        msg = "sysload=%.2f cputime=%.3f" % (self.last_load_avg,
                                             self.total_process_time)
        # Sample both memory metrics from the same read.
        meminfo = {}
        if self.mem_file is not None:
            try:
                self.mem_file.seek(0)
                data = self.mem_file.read()
            except OSError:
                pass
            else:
                for line in data.splitlines():
                    field, _, raw = line.partition(":")
                    if field not in ("MemAvailable", "MemFree", "Active(file)",
                                     "Inactive(file)", "KReclaimable", "SReclaimable"):
                        continue
                    try:
                        value = int(raw.split()[0])
                    except (IndexError, ValueError):
                        value = -1
                    meminfo[field] = value if value >= 0 else None

        memavail = meminfo.get("MemAvailable")
        if memavail is not None:
            self.last_mem_avail = memavail
            msg = "%s memavail=%d" % (msg, memavail)

        # KReclaimable already includes SReclaimable.
        reclaimable = meminfo.get("KReclaimable", meminfo.get("SReclaimable"))
        free_cache = (meminfo.get("MemFree"), meminfo.get("Active(file)"),
                      meminfo.get("Inactive(file)"), reclaimable)
        self.last_mem_free_cache = None if None in free_cache else sum(free_cache)
        return (False, msg)
    def get_status(self, eventtime):
        return {'sysload': self.last_load_avg,
                'cputime': self.total_process_time,
                'memavail': self.last_mem_avail,
                'mem_free_cache': self.last_mem_free_cache}

class PrinterStats:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.disabled = config.getboolean("disabled", False)
        reactor = self.printer.get_reactor()
        self.stats_cb = []
        self.stats_timer = reactor.register_timer(self.generate_stats)
        self.printer.register_event_handler("klippy:ready", self.handle_ready)
    def handle_ready(self):
        self.stats_cb = [o.stats for n, o in self.printer.lookup_objects()
                         if hasattr(o, 'stats')]
        if self.printer.get_start_args().get('debugoutput') is None:
            reactor = self.printer.get_reactor()
            reactor.update_timer(self.stats_timer, reactor.NOW)
    def generate_stats(self, eventtime):
        # We should call actual callbacks, otherwise data will be queued untill overflow
        stats = [cb(eventtime) for cb in self.stats_cb]

        if not self.disabled:
            if max([s[0] for s in stats]):
                logging.info("Stats %.1f: %s", eventtime,' '.join([s[1] for s in stats]))

        return eventtime + 1.

def load_config(config):
    config.get_printer().add_object('system_stats', PrinterSysStats(config))
    return PrinterStats(config)
