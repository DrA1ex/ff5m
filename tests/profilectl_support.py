# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
# This file may be distributed under the terms of the GNU GPLv3 license.

"""Fake printer environment for the public shell profile utility."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1] / '.bin/tools/overclocking'
FIXTURES = Path(__file__).resolve().parent / 'fixtures/profilectl'
TOOL = ROOT / 'bin/t113-profilectl'
HOST_BIN = Path(os.environ['T113_HOST_BIN']).resolve() if os.environ.get('T113_HOST_BIN') else None
FGX = HOST_BIN / 'fgx-fdt' if HOST_BIN else None
STOCK = ROOT / 'profiles/source/sunxi-stock.dtb'
STOCK_ENV = ROOT / 'profiles/source/stock-uboot.env'
FWCFG = FIXTURES / 'fw_env.config'
DMESG = FIXTURES / 'clean-dmesg.txt'

STOCK_SHA = '27501dc48c5ad4c71e3a12742e3ef2a162d6a2d8285bbbac82488c7fade79e21'
STOCK_BOOT = 'run setargs_mmc boot_dsp0 boot_uImage'
STOCK_SETARGS = 'setenv  bootargs earlycon=${earlycon} clk_ignore_unused initcall_debug=${initcall_debug} console=${console} loglevel=${loglevel} root=${mmc_root}  init=${init} partitions=${partitions} cma=${cma} snum=${snum} mac_addr=${mac} wifi_mac=${wifi_mac} bt_mac=${bt_mac} specialstr=${specialstr} gpt=1'
STOCK_DSP = 'sunxi_flash read 43000000 ${dsp0_partition};bootr 43000000 0 0'
STOCK_UIMAGE = 'part list mmc 0;fatload mmc 0:1 0x43000000 uImage;fatload mmc 0:1 0x44864e70 sunxi.dtb;fdt resize;bootm 0x43000000 - 0x44864e70'
MANAGED_BOOT = 'run setargs_mmc boot_dsp0 fgx_boot_current'
ONESHOT_BOOT = 'run fgx_boot_restore fgx_boot_save setargs_mmc boot_dsp0 fgx_boot_test'
TRANSITION_BOOT = 'run setargs_mmc boot_dsp0 fgx_boot_test'
CID_HS = '15010048533230304700000000000001'
CID_HS_2 = 'd6010335384133393811000000000002'
CID_LEGACY = '1501004d345846495500000000000003'
CPU_ALL = [480,720,912,1008,1104,1152,1200,1248,1296,1344,1392]


class Failure(Exception): pass

def check(cond, msg):
    if not cond: raise Failure(msg)

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class Fixture:
    def __init__(self, base, name, family='hs2001v8', cid=None):
        if HOST_BIN is None:
            raise unittest.SkipTest('Set T113_HOST_BIN to host-built fgx-fdt and fgx-lock')
        self.d=Path(base)/name; shutil.rmtree(self.d,ignore_errors=True); self.d.mkdir(parents=True)
        self.boot=self.d/'boot'; self.state=self.d/'state'; self.boot.mkdir(); self.state.mkdir()
        self.envdb=self.d/'environment.txt'; self.cid=self.d/'cid'; self.ios=self.d/'ios'; self.bootid=self.d/'boot-id'
        self.cpu=self.d/'cpu-cur'; self.cpumax=self.d/'cpu-max'; self.cpuavail=self.d/'cpu-avail'; self.cpudrv=self.d/'cpu-driver'; self.cputemp=self.d/'cpu-temp'; self.clk=self.d/'clk-summary'
        self.p1=self.d/'p1'; self.p2=self.d/'p2'; self.p3=self.d/'p3'; self.log=self.d/'env-write.log'; self.cache=self.d/'tmp-cache'; self.lock=self.d/'tmp-lock'
        self.envdb.write_text('\n'.join([
            f'bootcmd={STOCK_BOOT}',
            f'setargs_mmc={STOCK_SETARGS}',
            f'boot_dsp0={STOCK_DSP}',
            f'boot_uImage={STOCK_UIMAGE}',
            'ethaddr=02:00:00:00:00:01','serial#=fixture'])+'\n')
        shutil.copy2(STOCK,self.boot/'sunxi.dtb'); (self.boot/'uImage').write_bytes(b'fixture-uImage\n')
        for p,n in [(self.p1,3072),(self.p2,5120),(self.p3,7168)]: p.write_bytes(b'\0'*n)
        self.bootid.write_text('boot-a\n'); self.stock_cpu(); self.log.write_text('')
        self.family=family
        if cid is None: cid=CID_HS if family=='hs2001v8' else CID_LEGACY
        self.cid.write_text(cid+'\n')
        if family=='hs2001v8': self.set_ios(25,9,1)
        else:self.set_ios(25,0,0)
        self.sums = self.d / 'bundle-SHA256SUMS'
        names = ('bin/t113-profilectl', 'manifest-v2.json', 'profiles/source/stock-uboot.env',
                 'profiles/source/sunxi-stock.dtb', 'schema/manifest-v2.schema.json')
        entries = [(name, ROOT / name) for name in names]
        entries += [(str(HOST_BIN / name), HOST_BIN / name) for name in ('fgx-fdt', 'fgx-lock', 'fgx-stress')]
        self.sums.write_text(''.join(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + name + '\n'
                                    for name, path in entries))
        self.baseenv=os.environ.copy(); self.baseenv.update({
            'PATH':str(FIXTURES/'bin')+':'+os.environ.get('PATH',''), 'T113_BUNDLE_DIR':str(ROOT),
            'T113_BUNDLE_SUMS':str(self.sums), 'T113_FGX_FDT':str(FGX), 'T113_FGX_LOCK':str(HOST_BIN/'fgx-lock'), 'T113_TEST_MODE':'1', 'T113_FW_CONFIG':str(FWCFG), 'T113_DMESG_FILE':str(DMESG),
            'T113_FAKE_ENV_DB':str(self.envdb), 'T113_STATE_DIR':str(self.state), 'T113_BOOT_DIR':str(self.boot),
            'T113_DEV_BOOT':str(self.p1),'T113_DEV_ENV':str(self.p2),'T113_DEV_ENV_REDUND':str(self.p3),
            'T113_IOS_FILE':str(self.ios),'T113_CID_FILE':str(self.cid),'T113_BOOT_ID_FILE':str(self.bootid),
            'T113_CPU_CUR_FILE':str(self.cpu),'T113_CPU_MAX_FILE':str(self.cpumax),'T113_CPU_AVAIL_FILE':str(self.cpuavail),
            'T113_CPU_DRIVER_FILE':str(self.cpudrv),'T113_CLK_SUMMARY_FILE':str(self.clk),'T113_CPU_TEMP_FILE':str(self.cputemp), 'T113_ENV_WRITE_LOG':str(self.log),
            'T113_CACHE_DIR':str(self.cache), 'T113_LOCK_DIR':str(self.lock), 'T113_TEST_SKIP_SYNC':'1', 'T113_TEST_ASSUME_BUNDLE_VALID':'1', 'LC_ALL':'C'})
    def set_ios(self,mhz,timing,voltage):
        self.ios.write_text(f'''clock:          {mhz*1000000} Hz\nvdd:            21 (3.3 ~ 3.4 V)\nbus mode:       2 (push-pull)\nchip select:    0 (don't care)\npower mode:     2 (on)\nbus width:      2 (4 bits)\ntiming spec:    {timing} (fixture)\nsignal voltage: {voltage} (fixture)\ndriver type:    0 (driver type B)\n''')
    def env(self):
        out={}
        for line in self.envdb.read_text().splitlines():
            if '=' in line: k,v=line.split('=',1); out[k]=v
        return out
    def setenv(self,k,v=None):
        e=self.env(); e.pop(k,None)
        if v is not None: e[k]=v
        self.envdb.write_text(''.join(f'{a}={b}\n' for a,b in e.items()))
    def run(self,*args, ok=True, extra=None):
        env=self.baseenv.copy(); env.update(extra or {})
        p=subprocess.run([str(TOOL),*map(str,args)],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                         timeout=30)
        if ok and p.returncode!=0: raise Failure(f"command failed ({p.returncode}): {' '.join(args)}\n{p.stdout}")
        if not ok and p.returncode==0: raise Failure(f"command unexpectedly passed: {' '.join(args)}\n{p.stdout}")
        return p
    def clock(self,mhz):
        self.clk.write_text(f'   clock  enable_cnt  prepare_cnt  protect_cnt  rate  accuracy  phase  duty\n'
                            f'    pll-cpux  1  1  0  {mhz*1000000}  0  0  50000\n          cpux  1  1  0  {mhz*1000000}  0  0  50000\n')
    def stock_cpu(self,mhz=1008):
        # Stock vdd-cpu never registers, so there is no cpufreq policy; only the PLL rate is observable.
        for p in (self.cpu,self.cpumax,self.cpuavail,self.cpudrv):
            if p.exists(): p.unlink()
        self.clock(mhz)
    def policy(self,mhz,temp=45000,current=None):
        self.clock(current or mhz)
        self.cpumax.write_text(str(mhz*1000)+'\n'); self.cpuavail.write_text(' '.join(str(x*1000) for x in CPU_ALL)+'\n'); self.cpudrv.write_text('cpufreq-dt\n'); self.cputemp.write_text(str(temp)+'\n'); self.cpu.write_text(str((current or mhz)*1000)+'\n')
    def simulate_candidate_boot(self,bootid='boot-b', cpu_mhz=None, emmc=None):
        cand=meta(self.state/'candidate'); fam=cand['family']; cpu= cand['cpu_profile']; emmc=int(cand['emmc_mhz']) if emmc is None else emmc
        self.setenv('bootcmd',cand['fallback_bootcmd']); self.bootid.write_text(bootid+'\n')
        if cpu=='stock': self.stock_cpu()
        else: self.policy(int(cpu) if cpu_mhz is None else cpu_mhz, current=(int(cpu) if cpu_mhz is None else cpu_mhz))
        if fam=='hs2001v8': self.set_ios(emmc,9,1)
        elif emmc==25:self.set_ios(25,0,0)
        else:self.set_ios(emmc,1,0)
    def arm_verify_enable(self,axis,value,bootid='boot-b'):
        self.run('--apply','test',axis,value); self.simulate_candidate_boot(bootid); self.run('--apply','verify'); self.run('--apply','enable')

def meta(p):
    d={}
    for line in Path(p).read_text().splitlines():
        if '=' in line:k,v=line.split('=',1);d[k]=v
    return d

def prepare_verified_managed(tmp,name):
    f=Fixture(tmp,name,'hs2001v8'); f.arm_verify_enable('cpu','1.2','boot-b'); f.run('--apply','test','cpu','1.3'); f.simulate_candidate_boot('boot-c'); f.run('--apply','verify'); return f

