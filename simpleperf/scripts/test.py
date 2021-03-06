#!/usr/bin/env python
#
# Copyright (C) 2017 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""test.py: Tests for simpleperf python scripts.

These are smoke tests Using examples to run python scripts.
For each example, we go through the steps of running each python script.
Examples are collected from simpleperf/demo, which includes:
  SimpleperfExamplePureJava
  SimpleperfExampleWithNative
  SimpleperfExampleOfKotlin

Tested python scripts include:
  app_profiler.py
  report.py
  annotate.py
  report_sample.py
  pprof_proto_generator.py
  report_html.py

Test using both `adb root` and `adb unroot`.

"""

import os
import re
import shutil
import signal
import sys
import tempfile
import time
import unittest

from app_profiler import NativeLibDownloader
from simpleperf_report_lib import ReportLib
from utils import *

has_google_protobuf = True
try:
    import google.protobuf
except:
    has_google_protobuf = False

inferno_script = os.path.join(get_script_dir(), "inferno.bat" if is_windows() else "./inferno.sh")

support_trace_offcpu = None

def is_trace_offcpu_supported():
    global support_trace_offcpu
    if support_trace_offcpu is None:
        adb = AdbHelper()
        adb.check_run_and_return_output(['push',
                                         'bin/android/%s/simpleperf' % adb.get_device_arch(),
                                         "/data/local/tmp"])
        adb.check_run_and_return_output(['shell', 'chmod', 'a+x', '/data/local/tmp/simpleperf'])
        output = adb.check_run_and_return_output(['shell', '/data/local/tmp/simpleperf', 'list',
                                                  '--show-features'])
        support_trace_offcpu = 'trace-offcpu' in output
    return support_trace_offcpu

def build_testdata():
    """ Collect testdata from ../testdata and ../demo. """
    from_testdata_path = os.path.join('..', 'testdata')
    from_demo_path = os.path.join('..', 'demo')
    from_script_testdata_path = 'script_testdata'
    if (not os.path.isdir(from_testdata_path) or not os.path.isdir(from_demo_path) or
        not from_script_testdata_path):
        return
    copy_testdata_list = ['perf_with_symbols.data', 'perf_with_trace_offcpu.data',
                          'perf_with_tracepoint_event.data', 'perf_with_interpreter_frames.data']
    copy_demo_list = ['SimpleperfExamplePureJava', 'SimpleperfExampleWithNative',
                      'SimpleperfExampleOfKotlin']

    testdata_path = "testdata"
    remove(testdata_path)
    os.mkdir(testdata_path)
    for testdata in copy_testdata_list:
        shutil.copy(os.path.join(from_testdata_path, testdata), testdata_path)
    for demo in copy_demo_list:
        shutil.copytree(os.path.join(from_demo_path, demo), os.path.join(testdata_path, demo))
    for f in os.listdir(from_script_testdata_path):
        shutil.copy(os.path.join(from_script_testdata_path, f), testdata_path)

class TestBase(unittest.TestCase):
    def run_cmd(self, args, return_output=False):
        if args[0].endswith('.py'):
            args = [sys.executable] + args
        use_shell = args[0].endswith('.bat')
        try:
            if not return_output:
                returncode = subprocess.call(args, shell=use_shell)
            else:
                subproc = subprocess.Popen(args, stdout=subprocess.PIPE, shell=use_shell)
                (output_data, _) = subproc.communicate()
                returncode = subproc.returncode
        except:
            returncode = None
        self.assertEqual(returncode, 0, msg="failed to run cmd: %s" % args)
        if return_output:
            return output_data


class TestExampleBase(TestBase):
    @classmethod
    def prepare(cls, example_name, package_name, activity_name, abi=None, adb_root=False):
        cls.adb = AdbHelper(enable_switch_to_root=adb_root)
        cls.example_path = os.path.join("testdata", example_name)
        if not os.path.isdir(cls.example_path):
            log_fatal("can't find " + cls.example_path)
        for root, _, files in os.walk(cls.example_path):
            if 'app-profiling.apk' in files:
                cls.apk_path = os.path.join(root, 'app-profiling.apk')
                break
        if not hasattr(cls, 'apk_path'):
            log_fatal("can't find app-profiling.apk under " + cls.example_path)
        cls.package_name = package_name
        cls.activity_name = activity_name
        cls.abi = "arm64"
        if abi and abi != "arm64" and abi.find("arm") != -1:
            cls.abi = "arm"
        args = ["install", "-r"]
        if abi:
            args += ["--abi", abi]
        args.append(cls.apk_path)
        cls.adb.check_run(args)
        cls.adb_root = adb_root
        cls.compiled = False
        cls.has_perf_data_for_report = False
        android_version = cls.adb.get_android_version()
        # On Android >= P (version 9), we can profile JITed and interpreted Java code.
        # So only compile Java code on Android <= O (version 8).
        cls.use_compiled_java_code = android_version <= 8

    def setUp(self):
        if self.id().find('TraceOffCpu') != -1 and not is_trace_offcpu_supported():
            self.skipTest('trace-offcpu is not supported on device')
        cls = self.__class__
        if not cls.has_perf_data_for_report:
            cls.has_perf_data_for_report = True
            self.run_app_profiler()
            shutil.copy('perf.data', 'perf.data_for_report')
            remove('binary_cache_for_report')
            shutil.copytree('binary_cache', 'binary_cache_for_report')
        else:
            shutil.copy('perf.data_for_report', 'perf.data')
            remove('binary_cache')
            shutil.copytree('binary_cache_for_report', 'binary_cache')

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'test_result') and cls.test_result and not cls.test_result.wasSuccessful():
            return
        if hasattr(cls, 'package_name'):
            cls.adb.check_run(["uninstall", cls.package_name])
        remove("binary_cache")
        remove("annotated_files")
        remove("perf.data")
        remove("report.txt")
        remove("pprof.profile")
        if cls.has_perf_data_for_report:
            cls.has_perf_data_for_report = False
            remove('perf.data_for_report')
            remove('binary_cache_for_report')

    def run(self, result=None):
        self.__class__.test_result = result
        super(TestExampleBase, self).run(result)

    def run_app_profiler(self, record_arg="-g --duration 10", build_binary_cache=True,
                         start_activity=True):
        args = ['app_profiler.py', '--app', self.package_name, '-r', record_arg, '-o', 'perf.data']
        if not build_binary_cache:
            args.append("-nb")
        if self.use_compiled_java_code and not self.__class__.compiled:
            args.append('--compile_java_code')
            self.__class__.compiled = True
        if start_activity:
            args += ["-a", self.activity_name]
        args += ["-lib", self.example_path]
        if not self.adb_root:
            args.append("--disable_adb_root")
        self.run_cmd(args)
        self.check_exist(file="perf.data")
        if build_binary_cache:
            self.check_exist(dir="binary_cache")

    def check_exist(self, file=None, dir=None):
        if file:
            self.assertTrue(os.path.isfile(file), file)
        if dir:
            self.assertTrue(os.path.isdir(dir), dir)

    def check_file_under_dir(self, dir, file):
        self.check_exist(dir=dir)
        for _, _, files in os.walk(dir):
            for f in files:
                if f == file:
                    return
        self.fail("Failed to call check_file_under_dir(dir=%s, file=%s)" % (dir, file))


    def check_strings_in_file(self, file, strings):
        self.check_exist(file=file)
        with open(file, 'r') as fh:
            self.check_strings_in_content(fh.read(), strings)

    def check_strings_in_content(self, content, strings):
        for s in strings:
            self.assertNotEqual(content.find(s), -1, "s: %s, content: %s" % (s, content))

    def check_annotation_summary(self, summary_file, check_entries):
        """ check_entries is a list of (name, accumulated_period, period).
            This function checks for each entry, if the line containing [name]
            has at least required accumulated_period and period.
        """
        self.check_exist(file=summary_file)
        with open(summary_file, 'r') as fh:
            summary = fh.read()
        fulfilled = [False for x in check_entries]
        if not hasattr(self, "summary_check_re"):
            self.summary_check_re = re.compile(r'accumulated_period:\s*([\d.]+)%.*period:\s*([\d.]+)%')
        for line in summary.split('\n'):
            for i in range(len(check_entries)):
                (name, need_acc_period, need_period) = check_entries[i]
                if not fulfilled[i] and name in line:
                    m = self.summary_check_re.search(line)
                    if m:
                        acc_period = float(m.group(1))
                        period = float(m.group(2))
                        if acc_period >= need_acc_period and period >= need_period:
                            fulfilled[i] = True
        self.assertEqual(len(fulfilled), sum([int(x) for x in fulfilled]), fulfilled)

    def check_inferno_report_html(self, check_entries, file="report.html"):
        self.check_exist(file=file)
        with open(file, 'r') as fh:
            data = fh.read()
        fulfilled = [False for _ in check_entries]
        for line in data.split('\n'):
            # each entry is a (function_name, min_percentage) pair.
            for i, entry in enumerate(check_entries):
                if fulfilled[i] or line.find(entry[0]) == -1:
                    continue
                m = re.search(r'(\d+\.\d+)%', line)
                if m and float(m.group(1)) >= entry[1]:
                    fulfilled[i] = True
                    break
        self.assertEqual(fulfilled, [True for x in check_entries])

    def common_test_app_profiler(self):
        self.run_cmd(["app_profiler.py", "-h"])
        remove("binary_cache")
        self.run_app_profiler(build_binary_cache=False)
        self.assertFalse(os.path.isdir("binary_cache"))
        args = ["binary_cache_builder.py"]
        if not self.adb_root:
            args.append("--disable_adb_root")
        self.run_cmd(args)
        self.check_exist(dir="binary_cache")
        remove("binary_cache")
        self.run_app_profiler(build_binary_cache=True)
        self.run_app_profiler()
        self.run_app_profiler(start_activity=False)

    def common_test_report(self):
        self.run_cmd(["report.py", "-h"])
        self.run_cmd(["report.py"])
        self.run_cmd(["report.py", "-i", "perf.data"])
        self.run_cmd(["report.py", "-g"])
        self.run_cmd(["report.py", "--self-kill-for-testing",  "-g", "--gui"])

    def common_test_annotate(self):
        self.run_cmd(["annotate.py", "-h"])
        remove("annotated_files")
        self.run_cmd(["annotate.py", "-s", self.example_path])
        self.check_exist(dir="annotated_files")

    def common_test_report_sample(self, check_strings):
        self.run_cmd(["report_sample.py", "-h"])
        self.run_cmd(["report_sample.py"])
        output = self.run_cmd(["report_sample.py", "perf.data"], return_output=True)
        self.check_strings_in_content(output, check_strings)

    def common_test_pprof_proto_generator(self, check_strings_with_lines,
                                          check_strings_without_lines):
        if not has_google_protobuf:
            log_info('Skip test for pprof_proto_generator because google.protobuf is missing')
            return
        self.run_cmd(["pprof_proto_generator.py", "-h"])
        self.run_cmd(["pprof_proto_generator.py"])
        remove("pprof.profile")
        self.run_cmd(["pprof_proto_generator.py", "-i", "perf.data", "-o", "pprof.profile"])
        self.check_exist(file="pprof.profile")
        self.run_cmd(["pprof_proto_generator.py", "--show"])
        output = self.run_cmd(["pprof_proto_generator.py", "--show", "pprof.profile"],
                              return_output=True)
        self.check_strings_in_content(output, check_strings_with_lines +
                                              ["has_line_numbers: True"])
        remove("binary_cache")
        self.run_cmd(["pprof_proto_generator.py"])
        output = self.run_cmd(["pprof_proto_generator.py", "--show", "pprof.profile"],
                              return_output=True)
        self.check_strings_in_content(output, check_strings_without_lines +
                                              ["has_line_numbers: False"])

    def common_test_inferno(self):
        self.run_cmd([inferno_script, "-h"])
        remove("perf.data")
        append_args = [] if self.adb_root else ["--disable_adb_root"]
        self.run_cmd([inferno_script, "-p", self.package_name, "-t", "3"] + append_args)
        self.check_exist(file="perf.data")
        self.run_cmd([inferno_script, "-p", self.package_name, "-f", "1000", "-du", "-t", "1"] +
                     append_args)
        self.run_cmd([inferno_script, "-p", self.package_name, "-e", "100000 cpu-cycles",
                      "-t", "1"] + append_args)
        self.run_cmd([inferno_script, "-sc"])

    def common_test_report_html(self):
        self.run_cmd(['report_html.py', '-h'])
        self.run_cmd(['report_html.py'])
        self.run_cmd(['report_html.py', '--add_source_code', '--source_dirs', 'testdata'])
        self.run_cmd(['report_html.py', '--add_disassembly'])
        # Test with multiple perf.data.
        shutil.move('perf.data', 'perf2.data')
        self.run_app_profiler(record_arg='-g -f 1000 --duration 3 -e task-clock:u')
        self.run_cmd(['report_html.py', '-i', 'perf.data', 'perf2.data'])
        remove('perf2.data')


class TestExamplePureJava(TestExampleBase):
    @classmethod
    def setUpClass(cls):
        cls.prepare("SimpleperfExamplePureJava",
                    "com.example.simpleperf.simpleperfexamplepurejava",
                    ".MainActivity")

    def test_app_profiler(self):
        self.common_test_app_profiler()

    def test_app_profiler_profile_from_launch(self):
        self.run_app_profiler(start_activity=True, build_binary_cache=False)
        self.run_cmd(["report.py", "-g", "-o", "report.txt"])
        self.check_strings_in_file("report.txt",
            ["com.example.simpleperf.simpleperfexamplepurejava.MainActivity$1.run()",
             "__start_thread"])

    def test_app_profiler_multiprocesses(self):
        self.adb.check_run(['shell', 'am', 'force-stop', self.package_name])
        self.adb.check_run(['shell', 'am', 'start', '-n',
                            self.package_name + '/.MultiProcessActivity'])
        # Wait until both MultiProcessActivity and MultiProcessService set up.
        time.sleep(3)
        self.run_app_profiler(start_activity=False)
        self.run_cmd(["report.py", "-o", "report.txt"])
        self.check_strings_in_file("report.txt", ["BusyService", "BusyThread"])

    def test_app_profiler_with_ctrl_c(self):
        if is_windows():
            return
        self.adb.check_run(['shell', 'am', 'start', '-n', self.package_name + '/.MainActivity'])
        time.sleep(1)
        args = [sys.executable, "app_profiler.py", "--app", self.package_name,
                "-r", "--duration 10000", "--disable_adb_root"]
        subproc = subprocess.Popen(args)
        time.sleep(3)

        subproc.send_signal(signal.SIGINT)
        subproc.wait()
        self.assertEqual(subproc.returncode, 0)
        self.run_cmd(["report.py"])

    def test_app_profiler_stop_after_app_exit(self):
        self.adb.check_run(['shell', 'am', 'start', '-n', self.package_name + '/.MainActivity'])
        time.sleep(1)
        subproc = subprocess.Popen([sys.executable, 'app_profiler.py', '--app', self.package_name,
                                    '-r', '--duration 10000', '--disable_adb_root'])
        time.sleep(3)
        self.adb.check_run(['shell', 'am', 'force-stop', self.package_name])
        subproc.wait()
        self.assertEqual(subproc.returncode, 0)
        self.run_cmd(["report.py"])

    def test_report(self):
        self.common_test_report()
        self.run_cmd(["report.py", "-g", "-o", "report.txt"])
        self.check_strings_in_file("report.txt",
            ["com.example.simpleperf.simpleperfexamplepurejava.MainActivity$1.run()",
             "__start_thread"])

    def test_profile_with_process_id(self):
        self.adb.check_run(['shell', 'am', 'start', '-n', self.package_name + '/.MainActivity'])
        time.sleep(1)
        pid = self.adb.check_run_and_return_output(['shell', 'pidof',
                'com.example.simpleperf.simpleperfexamplepurejava']).strip()
        self.run_app_profiler(start_activity=False, record_arg='-g --duration 10 -p ' + pid)
        self.run_cmd(["report.py", "-g", "-o", "report.txt"])
        self.check_strings_in_file("report.txt",
            ["com.example.simpleperf.simpleperfexamplepurejava.MainActivity$1.run()",
             "__start_thread"])

    def test_annotate(self):
        self.common_test_annotate()
        if not self.use_compiled_java_code:
            # Currently annotating Java code is only supported when the Java code is compiled.
            return
        self.check_file_under_dir("annotated_files", "MainActivity.java")
        summary_file = os.path.join("annotated_files", "summary")
        self.check_annotation_summary(summary_file,
            [("MainActivity.java", 80, 80),
             ("run", 80, 0),
             ("callFunction", 0, 0),
             ("line 23", 80, 0)])

    def test_report_sample(self):
        self.common_test_report_sample(
            ["com.example.simpleperf.simpleperfexamplepurejava.MainActivity$1.run()",
             "__start_thread"])

    def test_pprof_proto_generator(self):
        check_strings_with_lines = []
        if self.use_compiled_java_code:
            check_strings_with_lines = [
                "com/example/simpleperf/simpleperfexamplepurejava/MainActivity.java",
                "run"]
        self.common_test_pprof_proto_generator(
            check_strings_with_lines=check_strings_with_lines,
            check_strings_without_lines=
                ["com.example.simpleperf.simpleperfexamplepurejava.MainActivity$1.run()"])

    def test_inferno(self):
        self.common_test_inferno()
        self.run_app_profiler()
        self.run_cmd([inferno_script, "-sc"])
        self.check_inferno_report_html(
            [('com.example.simpleperf.simpleperfexamplepurejava.MainActivity$1.run()', 80)])
        self.run_cmd([inferno_script, "-sc", "-o", "report2.html"])
        self.check_inferno_report_html(
            [('com.example.simpleperf.simpleperfexamplepurejava.MainActivity$1.run()', 80)],
            "report2.html")
        remove("report2.html")

    def test_inferno_in_another_dir(self):
        test_dir = 'inferno_testdir'
        saved_dir = os.getcwd()
        remove(test_dir)
        os.mkdir(test_dir)
        os.chdir(test_dir)
        self.run_cmd(['python', os.path.join(saved_dir, 'app_profiler.py'),
                      '--app', self.package_name, '-r', '-e task-clock:u -g --duration 3'])
        self.check_exist(file="perf.data")
        self.run_cmd([inferno_script, "-sc"])
        os.chdir(saved_dir)
        remove(test_dir)

    def test_report_html(self):
        self.common_test_report_html()

    def test_run_simpleperf_without_usb_connection(self):
        self.adb.check_run(['shell', 'am', 'start', '-n', self.package_name + '/.MainActivity'])
        self.run_cmd(['run_simpleperf_without_usb_connection.py', 'start', '-p',
                      self.package_name, '--size_limit', '1M'])
        self.adb.check_run(['kill-server'])
        time.sleep(3)
        self.run_cmd(['run_simpleperf_without_usb_connection.py', 'stop'])
        self.check_exist(file="perf.data")
        self.run_cmd(["report.py", "-g", "-o", "report.txt"])


class TestExamplePureJavaRoot(TestExampleBase):
    @classmethod
    def setUpClass(cls):
        cls.prepare("SimpleperfExamplePureJava",
                    "com.example.simpleperf.simpleperfexamplepurejava",
                    ".MainActivity",
                    adb_root=True)

    def test_app_profiler(self):
        self.common_test_app_profiler()


class TestExamplePureJavaTraceOffCpu(TestExampleBase):
    @classmethod
    def setUpClass(cls):
        cls.prepare("SimpleperfExamplePureJava",
                    "com.example.simpleperf.simpleperfexamplepurejava",
                    ".SleepActivity")

    def test_smoke(self):
        self.run_app_profiler(record_arg="-g -f 1000 --duration 10 -e cpu-cycles:u --trace-offcpu")
        self.run_cmd(["report.py", "-g", "-o", "report.txt"])
        self.check_strings_in_file("report.txt",
            ["com.example.simpleperf.simpleperfexamplepurejava.SleepActivity$1.run",
             "com.example.simpleperf.simpleperfexamplepurejava.SleepActivity$1.RunFunction",
             "com.example.simpleperf.simpleperfexamplepurejava.SleepActivity$1.SleepFunction"
             ])
        remove("annotated_files")
        self.run_cmd(["annotate.py", "-s", self.example_path])
        self.check_exist(dir="annotated_files")
        if self.use_compiled_java_code:
            self.check_file_under_dir("annotated_files", "SleepActivity.java")
            summary_file = os.path.join("annotated_files", "summary")
            self.check_annotation_summary(summary_file,
               [("SleepActivity.java", 80, 20),
                ("run", 80, 0),
                ("RunFunction", 20, 20),
                ("SleepFunction", 20, 0),
                ("line 24", 20, 0),
                ("line 32", 20, 0)])
        self.run_cmd([inferno_script, "-sc"])
        self.check_inferno_report_html(
            [('com.example.simpleperf.simpleperfexamplepurejava.SleepActivity$1.run', 80),
             ('com.example.simpleperf.simpleperfexamplepurejava.SleepActivity$1.RunFunction',
              20),
             ('com.example.simpleperf.simpleperfexamplepurejava.SleepActivity$1.SleepFunction',
              20)])


class TestExampleWithNative(TestExampleBase):
    @classmethod
    def setUpClass(cls):
        cls.prepare("SimpleperfExampleWithNative",
                    "com.example.simpleperf.simpleperfexamplewithnative",
                    ".MainActivity")

    def test_app_profiler(self):
        self.common_test_app_profiler()

    def test_app_profiler_profile_from_launch(self):
        self.run_app_profiler(start_activity=True, build_binary_cache=False)
        self.run_cmd(["report.py", "-g", "-o", "report.txt"])
        self.check_strings_in_file("report.txt",
            ["BusyLoopThread",
             "__start_thread"])

    def test_report(self):
        self.common_test_report()
        self.run_cmd(["report.py", "-g", "-o", "report.txt"])
        self.check_strings_in_file("report.txt",
            ["BusyLoopThread",
             "__start_thread"])

    def test_annotate(self):
        self.common_test_annotate()
        self.check_file_under_dir("annotated_files", "native-lib.cpp")
        summary_file = os.path.join("annotated_files", "summary")
        self.check_annotation_summary(summary_file,
            [("native-lib.cpp", 20, 0),
             ("BusyLoopThread", 20, 0),
             ("line 46", 20, 0)])

    def test_report_sample(self):
        self.common_test_report_sample(
            ["BusyLoopThread",
             "__start_thread"])

    def test_pprof_proto_generator(self):
        self.common_test_pprof_proto_generator(
            check_strings_with_lines=
                ["native-lib.cpp",
                 "BusyLoopThread"],
            check_strings_without_lines=
                ["BusyLoopThread"])

    def test_inferno(self):
        self.common_test_inferno()
        self.run_app_profiler()
        self.run_cmd([inferno_script, "-sc"])
        self.check_inferno_report_html([('BusyLoopThread', 20)])

    def test_report_html(self):
        self.common_test_report_html()


class TestExampleWithNativeRoot(TestExampleBase):
    @classmethod
    def setUpClass(cls):
        cls.prepare("SimpleperfExampleWithNative",
                    "com.example.simpleperf.simpleperfexamplewithnative",
                    ".MainActivity",
                    adb_root=True)

    def test_app_profiler(self):
        self.common_test_app_profiler()


class TestExampleWithNativeTraceOffCpu(TestExampleBase):
    @classmethod
    def setUpClass(cls):
        cls.prepare("SimpleperfExampleWithNative",
                    "com.example.simpleperf.simpleperfexamplewithnative",
                    ".SleepActivity")

    def test_smoke(self):
        self.run_app_profiler(record_arg="-g -f 1000 --duration 10 -e cpu-cycles:u --trace-offcpu")
        self.run_cmd(["report.py", "-g", "--comms", "SleepThread", "-o", "report.txt"])
        self.check_strings_in_file("report.txt",
            ["SleepThread(void*)",
             "RunFunction()",
             "SleepFunction(unsigned long long)"])
        remove("annotated_files")
        self.run_cmd(["annotate.py", "-s", self.example_path, "--comm", "SleepThread"])
        self.check_exist(dir="annotated_files")
        self.check_file_under_dir("annotated_files", "native-lib.cpp")
        summary_file = os.path.join("annotated_files", "summary")
        self.check_annotation_summary(summary_file,
            [("native-lib.cpp", 80, 20),
             ("SleepThread", 80, 0),
             ("RunFunction", 20, 20),
             ("SleepFunction", 20, 0),
             ("line 73", 20, 0),
             ("line 83", 20, 0)])
        self.run_cmd([inferno_script, "-sc"])
        self.check_inferno_report_html([('SleepThread', 80),
                                        ('RunFunction', 20),
                                        ('SleepFunction', 20)])


class TestExampleWithNativeJniCall(TestExampleBase):
    @classmethod
    def setUpClass(cls):
        cls.prepare("SimpleperfExampleWithNative",
                    "com.example.simpleperf.simpleperfexamplewithnative",
                    ".MixActivity")

    def test_smoke(self):
        self.run_app_profiler()
        self.run_cmd(["report.py", "-g", "--comms", "BusyThread", "-o", "report.txt"])
        self.check_strings_in_file("report.txt",
            ["void com.example.simpleperf.simpleperfexamplewithnative.MixActivity$1.run()",
             "int com.example.simpleperf.simpleperfexamplewithnative.MixActivity.callFunction(int)",
             "Java_com_example_simpleperf_simpleperfexamplewithnative_MixActivity_callFunction"])
        remove("annotated_files")
        self.run_cmd(["annotate.py", "-s", self.example_path, "--comm", "BusyThread"])
        self.check_exist(dir="annotated_files")
        self.check_file_under_dir("annotated_files", "native-lib.cpp")
        summary_file = os.path.join("annotated_files", "summary")
        self.check_annotation_summary(summary_file,
            [("native-lib.cpp", 5, 0),
             ("line 40", 5, 0)])
        if self.use_compiled_java_code:
            self.check_file_under_dir("annotated_files", "MixActivity.java")
            self.check_annotation_summary(summary_file,
                [("MixActivity.java", 80, 0),
                 ("run", 80, 0),
                 ("line 26", 20, 0),
                 ("native-lib.cpp", 5, 0),
                 ("line 40", 5, 0)])

        self.run_cmd([inferno_script, "-sc"])


class TestExampleWithNativeForceArm(TestExampleWithNative):
    @classmethod
    def setUpClass(cls):
        cls.prepare("SimpleperfExampleWithNative",
                    "com.example.simpleperf.simpleperfexamplewithnative",
                    ".MainActivity",
                    abi="armeabi-v7a")


class TestExampleWithNativeForceArmRoot(TestExampleWithNativeRoot):
    @classmethod
    def setUpClass(cls):
        cls.prepare("SimpleperfExampleWithNative",
                    "com.example.simpleperf.simpleperfexamplewithnative",
                    ".MainActivity",
                    abi="armeabi-v7a",
                    adb_root=False)


class TestExampleWithNativeTraceOffCpuForceArm(TestExampleWithNativeTraceOffCpu):
    @classmethod
    def setUpClass(cls):
        cls.prepare("SimpleperfExampleWithNative",
                    "com.example.simpleperf.simpleperfexamplewithnative",
                    ".SleepActivity",
                    abi="armeabi-v7a")


class TestExampleOfKotlin(TestExampleBase):
    @classmethod
    def setUpClass(cls):
        cls.prepare("SimpleperfExampleOfKotlin",
                    "com.example.simpleperf.simpleperfexampleofkotlin",
                    ".MainActivity")

    def test_app_profiler(self):
        self.common_test_app_profiler()

    def test_app_profiler_profile_from_launch(self):
        self.run_app_profiler(start_activity=True, build_binary_cache=False)
        self.run_cmd(["report.py", "-g", "-o", "report.txt"])
        self.check_strings_in_file("report.txt",
            ["com.example.simpleperf.simpleperfexampleofkotlin.MainActivity$createBusyThread$1.run()",
             "__start_thread"])

    def test_report(self):
        self.common_test_report()
        self.run_cmd(["report.py", "-g", "-o", "report.txt"])
        self.check_strings_in_file("report.txt",
            ["com.example.simpleperf.simpleperfexampleofkotlin.MainActivity$createBusyThread$1.run()",
             "__start_thread"])

    def test_annotate(self):
        if not self.use_compiled_java_code:
            return
        self.common_test_annotate()
        self.check_file_under_dir("annotated_files", "MainActivity.kt")
        summary_file = os.path.join("annotated_files", "summary")
        self.check_annotation_summary(summary_file,
            [("MainActivity.kt", 80, 80),
             ("run", 80, 0),
             ("callFunction", 0, 0),
             ("line 19", 80, 0),
             ("line 25", 0, 0)])

    def test_report_sample(self):
        self.common_test_report_sample(
            ["com.example.simpleperf.simpleperfexampleofkotlin.MainActivity$createBusyThread$1.run()",
             "__start_thread"])

    def test_pprof_proto_generator(self):
        check_strings_with_lines = []
        if self.use_compiled_java_code:
            check_strings_with_lines = [
                "com/example/simpleperf/simpleperfexampleofkotlin/MainActivity.kt",
                "run"]
        self.common_test_pprof_proto_generator(
            check_strings_with_lines=check_strings_with_lines,
            check_strings_without_lines=
                ["com.example.simpleperf.simpleperfexampleofkotlin.MainActivity$createBusyThread$1.run()"])

    def test_inferno(self):
        self.common_test_inferno()
        self.run_app_profiler()
        self.run_cmd([inferno_script, "-sc"])
        self.check_inferno_report_html(
            [('com.example.simpleperf.simpleperfexampleofkotlin.MainActivity$createBusyThread$1.run()',
              80)])

    def test_report_html(self):
        self.common_test_report_html()


class TestExampleOfKotlinRoot(TestExampleBase):
    @classmethod
    def setUpClass(cls):
        cls.prepare("SimpleperfExampleOfKotlin",
                    "com.example.simpleperf.simpleperfexampleofkotlin",
                    ".MainActivity",
                    adb_root=True)

    def test_app_profiler(self):
        self.common_test_app_profiler()


class TestExampleOfKotlinTraceOffCpu(TestExampleBase):
    @classmethod
    def setUpClass(cls):
        cls.prepare("SimpleperfExampleOfKotlin",
                    "com.example.simpleperf.simpleperfexampleofkotlin",
                    ".SleepActivity")

    def test_smoke(self):
        self.run_app_profiler(record_arg="-g -f 1000 --duration 10 -e cpu-cycles:u --trace-offcpu")
        self.run_cmd(["report.py", "-g", "-o", "report.txt"])
        self.check_strings_in_file("report.txt",
            ["com.example.simpleperf.simpleperfexampleofkotlin.SleepActivity$createRunSleepThread$1.run",
             "com.example.simpleperf.simpleperfexampleofkotlin.SleepActivity$createRunSleepThread$1.RunFunction",
             "com.example.simpleperf.simpleperfexampleofkotlin.SleepActivity$createRunSleepThread$1.SleepFunction"
             ])
        if self.use_compiled_java_code:
            remove("annotated_files")
            self.run_cmd(["annotate.py", "-s", self.example_path])
            self.check_exist(dir="annotated_files")
            self.check_file_under_dir("annotated_files", "SleepActivity.kt")
            summary_file = os.path.join("annotated_files", "summary")
            self.check_annotation_summary(summary_file,
                [("SleepActivity.kt", 80, 20),
                 ("run", 80, 0),
                 ("RunFunction", 20, 20),
                 ("SleepFunction", 20, 0),
                 ("line 24", 20, 0),
                 ("line 32", 20, 0)])

        self.run_cmd([inferno_script, "-sc"])
        self.check_inferno_report_html(
            [('com.example.simpleperf.simpleperfexampleofkotlin.SleepActivity$createRunSleepThread$1.run',
              80),
             ('com.example.simpleperf.simpleperfexampleofkotlin.SleepActivity$createRunSleepThread$1.RunFunction',
              20),
             ('com.example.simpleperf.simpleperfexampleofkotlin.SleepActivity$createRunSleepThread$1.SleepFunction',
              20)])


class TestProfilingCmd(TestBase):
    def test_smoke(self):
        remove("perf.data")
        self.run_cmd(["app_profiler.py", "-cmd", "pm -l", "--disable_adb_root"])
        self.run_cmd(["report.py", "-g", "-o", "report.txt"])


class TestProfilingNativeProgram(TestBase):
    def test_smoke(self):
        adb = AdbHelper()
        if adb.switch_to_root():
            self.run_cmd(["app_profiler.py", "-np", "surfaceflinger"])
            self.run_cmd(["report.py", "-g", "-o", "report.txt"])
            self.run_cmd([inferno_script, "-sc"])
            self.run_cmd([inferno_script, "-np", "surfaceflinger"])


class TestReportLib(unittest.TestCase):
    def setUp(self):
        self.report_lib = ReportLib()
        self.report_lib.SetRecordFile(os.path.join('testdata', 'perf_with_symbols.data'))

    def tearDown(self):
        self.report_lib.Close()

    def test_build_id(self):
        build_id = self.report_lib.GetBuildIdForPath('/data/t2')
        self.assertEqual(build_id, '0x70f1fe24500fc8b0d9eb477199ca1ca21acca4de')

    def test_symbol(self):
        found_func2 = False
        while self.report_lib.GetNextSample():
            sample = self.report_lib.GetCurrentSample()
            symbol = self.report_lib.GetSymbolOfCurrentSample()
            if symbol.symbol_name == 'func2(int, int)':
                found_func2 = True
                self.assertEqual(symbol.symbol_addr, 0x4004ed)
                self.assertEqual(symbol.symbol_len, 0x14)
        self.assertTrue(found_func2)

    def test_sample(self):
        found_sample = False
        while self.report_lib.GetNextSample():
            sample = self.report_lib.GetCurrentSample()
            if sample.ip == 0x4004ff and sample.time == 7637889424953:
                found_sample = True
                self.assertEqual(sample.pid, 15926)
                self.assertEqual(sample.tid, 15926)
                self.assertEqual(sample.thread_comm, 't2')
                self.assertEqual(sample.cpu, 5)
                self.assertEqual(sample.period, 694614)
                event = self.report_lib.GetEventOfCurrentSample()
                self.assertEqual(event.name, 'cpu-cycles')
                callchain = self.report_lib.GetCallChainOfCurrentSample()
                self.assertEqual(callchain.nr, 0)
        self.assertTrue(found_sample)

    def test_meta_info(self):
        self.report_lib.SetRecordFile(os.path.join('testdata', 'perf_with_trace_offcpu.data'))
        meta_info = self.report_lib.MetaInfo()
        self.assertTrue("simpleperf_version" in meta_info)
        self.assertEqual(meta_info["system_wide_collection"], "false")
        self.assertEqual(meta_info["trace_offcpu"], "true")
        self.assertEqual(meta_info["event_type_info"], "cpu-cycles,0,0\nsched:sched_switch,2,47")
        self.assertTrue("product_props" in meta_info)

    def test_event_name_from_meta_info(self):
        self.report_lib.SetRecordFile(os.path.join('testdata', 'perf_with_tracepoint_event.data'))
        event_names = set()
        while self.report_lib.GetNextSample():
            event_names.add(self.report_lib.GetEventOfCurrentSample().name)
        self.assertTrue('sched:sched_switch' in event_names)
        self.assertTrue('cpu-cycles' in event_names)

    def test_record_cmd(self):
        self.report_lib.SetRecordFile(os.path.join('testdata', 'perf_with_trace_offcpu.data'))
        self.assertEqual(self.report_lib.GetRecordCmd(),
                         "/data/local/tmp/simpleperf record --trace-offcpu --duration 2 -g ./simpleperf_runtest_run_and_sleep64")

    def test_offcpu(self):
        self.report_lib.SetRecordFile(os.path.join('testdata', 'perf_with_trace_offcpu.data'))
        total_period = 0
        sleep_function_period = 0
        sleep_function_name = "SleepFunction(unsigned long long)"
        while self.report_lib.GetNextSample():
            sample = self.report_lib.GetCurrentSample()
            total_period += sample.period
            if self.report_lib.GetSymbolOfCurrentSample().symbol_name == sleep_function_name:
                sleep_function_period += sample.period
                continue
            callchain = self.report_lib.GetCallChainOfCurrentSample()
            for i in range(callchain.nr):
                if callchain.entries[i].symbol.symbol_name == sleep_function_name:
                    sleep_function_period += sample.period
                    break
            self.assertEqual(self.report_lib.GetEventOfCurrentSample().name, 'cpu-cycles')
        sleep_percentage = float(sleep_function_period) / total_period
        self.assertGreater(sleep_percentage, 0.30)

    def test_show_art_frames(self):
        def has_art_frame(report_lib):
            report_lib.SetRecordFile(os.path.join('testdata', 'perf_with_interpreter_frames.data'))
            result = False
            while report_lib.GetNextSample():
                callchain = report_lib.GetCallChainOfCurrentSample()
                for i in range(callchain.nr):
                    if callchain.entries[i].symbol.symbol_name == 'artMterpAsmInstructionStart':
                        result = True
                        break
            report_lib.Close()
            return result

        report_lib = ReportLib()
        self.assertFalse(has_art_frame(report_lib))
        report_lib = ReportLib()
        report_lib.ShowArtFrames(False)
        self.assertFalse(has_art_frame(report_lib))
        report_lib = ReportLib()
        report_lib.ShowArtFrames(True)
        self.assertTrue(has_art_frame(report_lib))


class TestRunSimpleperfOnDevice(TestBase):
    def test_smoke(self):
        self.run_cmd(['run_simpleperf_on_device.py', 'list', '--show-features'])


class TestTools(unittest.TestCase):
    def test_addr2nearestline(self):
        binary_cache_path = 'testdata'
        test_map = {
            '/simpleperf_runtest_two_functions_arm64': [
                {
                    'func_addr': 0x668,
                    'addr': 0x668,
                    'source': 'system/extras/simpleperf/runtest/two_functions.cpp:20',
                },
                {
                    'func_addr': 0x668,
                    'addr': 0x6a4,
                    'source': """system/extras/simpleperf/runtest/two_functions.cpp:7
                                 system/extras/simpleperf/runtest/two_functions.cpp:22""",
                },
            ],
            '/simpleperf_runtest_two_functions_arm': [
                {
                    'func_addr': 0x784,
                    'addr': 0x7b0,
                    'source': """system/extras/simpleperf/runtest/two_functions.cpp:14
                                 system/extras/simpleperf/runtest/two_functions.cpp:23""",
                },
                {
                    'func_addr': 0x784,
                    'addr': 0x7d0,
                    'source': """system/extras/simpleperf/runtest/two_functions.cpp:15
                                 system/extras/simpleperf/runtest/two_functions.cpp:23""",
                }
            ],
            '/simpleperf_runtest_two_functions_x86_64': [
                {
                    'func_addr': 0x840,
                    'addr': 0x840,
                    'source': 'system/extras/simpleperf/runtest/two_functions.cpp:7',
                },
                {
                    'func_addr': 0x920,
                    'addr': 0x94a,
                    'source': """system/extras/simpleperf/runtest/two_functions.cpp:7
                                 system/extras/simpleperf/runtest/two_functions.cpp:22""",
                }
            ],
            '/simpleperf_runtest_two_functions_x86': [
                {
                    'func_addr': 0x6d0,
                    'addr': 0x6da,
                    'source': 'system/extras/simpleperf/runtest/two_functions.cpp:14',
                },
                {
                    'func_addr': 0x710,
                    'addr': 0x749,
                    'source': """system/extras/simpleperf/runtest/two_functions.cpp:8
                                 system/extras/simpleperf/runtest/two_functions.cpp:22""",
                }
            ],
        }
        addr2line = Addr2Nearestline(None, binary_cache_path)
        for dso_path in test_map:
            test_addrs = test_map[dso_path]
            for test_addr in test_addrs:
                addr2line.add_addr(dso_path, test_addr['func_addr'], test_addr['addr'])
        addr2line.convert_addrs_to_lines()
        for dso_path in test_map:
            dso = addr2line.get_dso(dso_path)
            self.assertTrue(dso is not None)
            test_addrs = test_map[dso_path]
            for test_addr in test_addrs:
                source_str = test_addr['source']
                expected_source = []
                for line in source_str.split('\n'):
                    items = line.split(':')
                    expected_source.append((items[0].strip(), int(items[1])))
                actual_source = addr2line.get_addr_source(dso, test_addr['addr'])
                self.assertTrue(actual_source is not None)
                self.assertEqual(len(actual_source), len(expected_source))
                for i in range(len(expected_source)):
                    actual_file_path, actual_line = actual_source[i]
                    self.assertEqual(actual_file_path, expected_source[i][0])
                    self.assertEqual(actual_line, expected_source[i][1])

    def test_objdump(self):
        binary_cache_path = 'testdata'
        test_map = {
            '/simpleperf_runtest_two_functions_arm64': {
                'start_addr': 0x668,
                'len': 116,
                'expected_items': [
                    ('main():', 0),
                    ('system/extras/simpleperf/runtest/two_functions.cpp:20', 0),
                    (' 694:	add	x20, x20, #0x6de', 0x694),
                ],
            },
            '/simpleperf_runtest_two_functions_arm': {
                'start_addr': 0x784,
                'len': 80,
                'expected_items': [
                    ('main():', 0),
                    ('system/extras/simpleperf/runtest/two_functions.cpp:20', 0),
                    ('     7ae:	bne.n	7a6 <main+0x22>', 0x7ae),
                ],
            },
            '/simpleperf_runtest_two_functions_x86_64': {
                'start_addr': 0x920,
                'len': 201,
                'expected_items': [
                    ('main():', 0),
                    ('system/extras/simpleperf/runtest/two_functions.cpp:20', 0),
                    (' 96e:	mov    %edx,(%rbx,%rax,4)', 0x96e),
                ],
            },
            '/simpleperf_runtest_two_functions_x86': {
                'start_addr': 0x710,
                'len': 98,
                'expected_items': [
                    ('main():', 0),
                    ('system/extras/simpleperf/runtest/two_functions.cpp:20', 0),
                    (' 748:	cmp    $0x5f5e100,%ebp', 0x748),
                ],
            },
        }
        objdump = Objdump(None, binary_cache_path)
        for dso_path in test_map:
            dso_info = test_map[dso_path]
            disassemble_code = objdump.disassemble_code(dso_path, dso_info['start_addr'],
                                                        dso_info['len'])
            self.assertTrue(disassemble_code)
            for item in dso_info['expected_items']:
                self.assertTrue(item in disassemble_code)

    def test_readelf(self):
        test_map = {
           '/simpleperf_runtest_two_functions_arm64': {
               'arch': 'arm64',
               'build_id': '0xe8ecb3916d989dbdc068345c30f0c24300000000',
               'sections': ['.interp', '.note.android.ident', '.note.gnu.build-id', '.dynsym',
                            '.dynstr', '.gnu.hash', '.gnu.version', '.gnu.version_r', '.rela.dyn',
                            '.rela.plt', '.plt', '.text', '.rodata', '.eh_frame', '.eh_frame_hdr',
                            '.preinit_array', '.init_array', '.fini_array', '.dynamic', '.got',
                            '.got.plt', '.data', '.bss', '.comment', '.debug_str', '.debug_loc',
                            '.debug_abbrev', '.debug_info', '.debug_ranges', '.debug_macinfo',
                            '.debug_pubnames', '.debug_pubtypes', '.debug_line',
                            '.note.gnu.gold-version', '.symtab', '.strtab', '.shstrtab'],
           },
           '/simpleperf_runtest_two_functions_arm': {
               'arch': 'arm',
               'build_id': '0x718f5b36c4148ee1bd3f51af89ed2be600000000',
           },
           '/simpleperf_runtest_two_functions_x86_64': {
               'arch': 'x86_64',
           },
           '/simpleperf_runtest_two_functions_x86': {
               'arch': 'x86',
           }
        }
        readelf = ReadElf(None)
        for dso_path in test_map:
            dso_info = test_map[dso_path]
            path = 'testdata' + dso_path
            self.assertEqual(dso_info['arch'], readelf.get_arch(path))
            if 'build_id' in dso_info:
                self.assertEqual(dso_info['build_id'], readelf.get_build_id(path))
            if 'sections' in dso_info:
                self.assertEqual(dso_info['sections'], readelf.get_sections(path))
        self.assertEqual(readelf.get_arch('not_exist_file'), 'unknown')
        self.assertEqual(readelf.get_build_id('not_exist_file'), '')
        self.assertEqual(readelf.get_sections('not_exist_file'), [])


class TestNativeLibDownloader(unittest.TestCase):
    def test_smoke(self):
        self.adb = AdbHelper()
        # Sync all native libs on device.
        self.adb.run(['shell', 'rm', '-rf', '/data/local/tmp/native_libs'])
        downloader = NativeLibDownloader(None, 'arm64', self.adb)
        downloader.collect_native_libs_on_host(os.path.join(
            'testdata', 'SimpleperfExampleWithNative', 'app', 'build', 'intermediates', 'cmake',
            'profiling'))
        self.assertEqual(len(downloader.host_build_id_map), 2)
        for entry in downloader.host_build_id_map.values():
            self.assertEqual(entry.score, 3)
        downloader.collect_native_libs_on_device()
        self.assertEqual(len(downloader.device_build_id_map), 0)

        lib_list = downloader.host_build_id_map.items()
        for sync_count in [0, 1, 2]:
            build_id_map = {}
            for i in range(sync_count):
                build_id_map[lib_list[i][0]] = lib_list[i][1]
            downloader.host_build_id_map = build_id_map
            downloader.sync_natives_libs_on_device()
            downloader.collect_native_libs_on_device()
            self.assertEqual(len(downloader.device_build_id_map), sync_count)
            for i in range(len(lib_list)):
                build_id = lib_list[i][0]
                name = lib_list[i][1].name
                if i < sync_count:
                    self.assertTrue(build_id in downloader.device_build_id_map)
                    self.assertEqual(name, downloader.device_build_id_map[build_id])
                    self.assertTrue(self._is_lib_on_device(downloader.dir_on_device + name))
                else:
                    self.assertTrue(build_id not in downloader.device_build_id_map)
                    self.assertFalse(self._is_lib_on_device(downloader.dir_on_device + name))
            if sync_count == 1:
                self.adb.run(['pull', '/data/local/tmp/native_libs/build_id_list',
                              'build_id_list'])
                with open('build_id_list', 'rb') as fh:
                    self.assertEqual(fh.read(), '{}={}\n'.format(lib_list[0][0],
                                                                 lib_list[0][1].name))
                remove('build_id_list')
        self.adb.run(['shell', 'rm', '-rf', '/data/local/tmp/native_libs'])

    def _is_lib_on_device(self, path):
        return self.adb.run(['shell', 'ls', path])


def main():
    os.chdir(get_script_dir())
    build_testdata()
    if AdbHelper().get_android_version() < 7:
        log_info("Skip tests on Android version < N.")
        sys.exit(0)
    unittest.main(failfast=True)

if __name__ == '__main__':
    main()
