/*
 * Copyright (C) 2018 The Android Open Source Project
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "dso.h"

#include <gtest/gtest.h>

#include <android-base/file.h>
#include <android-base/stringprintf.h>
#include <android-base/test_utils.h>

#include "get_test_data.h"

using namespace simpleperf_dso_impl;

TEST(DebugElfFileFinder, use_build_id_list) {
  // Create a temp symdir with build_id_list.
  TemporaryDir tmpdir;
  TemporaryFile tmpfile(tmpdir.path);
  std::string data;
  ASSERT_TRUE(android::base::ReadFileToString(GetTestData(ELF_FILE), &data));
  ASSERT_TRUE(android::base::WriteStringToFile(data, tmpfile.path));
  BuildId build_id(ELF_FILE_BUILD_ID);
  std::string build_id_list = android::base::StringPrintf(
      "%s=%s\n", build_id.ToString().c_str(), android::base::Basename(tmpfile.path).c_str());
  std::string build_id_list_file = std::string(tmpdir.path) + "/build_id_list";
  ASSERT_TRUE(android::base::WriteStringToFile(build_id_list, build_id_list_file));

  DebugElfFileFinder finder;
  ASSERT_TRUE(finder.SetSymFsDir(tmpdir.path));
  ASSERT_EQ(finder.FindDebugFile("elf", false, build_id), std::string(tmpfile.path));
  unlink(build_id_list_file.c_str());
}

TEST(DebugElfFileFinder, concatenating_symfs_dir) {
  DebugElfFileFinder finder;
  ASSERT_TRUE(finder.SetSymFsDir(GetTestDataDir()));
  BuildId build_id(ELF_FILE_BUILD_ID);
  ASSERT_EQ(finder.FindDebugFile(ELF_FILE, false, build_id), GetTestDataDir() + ELF_FILE);
  std::string native_lib_in_apk = APK_FILE + "!/" + NATIVELIB_IN_APK;
  ASSERT_EQ(finder.FindDebugFile(native_lib_in_apk, false, native_lib_build_id),
            GetTestDataDir() + native_lib_in_apk);
}

TEST(DebugElfFileFinder, use_vdso) {
  DebugElfFileFinder finder;
  std::string fake_vdso32 = "fake_vdso32";
  std::string fake_vdso64 = "fake_vdso64";
  finder.SetVdsoFile(fake_vdso32, false);
  finder.SetVdsoFile(fake_vdso64, true);
  BuildId build_id;
  ASSERT_EQ(finder.FindDebugFile("[vdso]", false, build_id), fake_vdso32);
  ASSERT_EQ(finder.FindDebugFile("[vdso]", true, build_id), fake_vdso64);
}

TEST(dso, dex_file_dso) {
#if defined(__linux__)
  for (DsoType dso_type : {DSO_DEX_FILE, DSO_ELF_FILE}) {
    std::unique_ptr<Dso> dso = Dso::CreateDso(dso_type, GetTestData("base.vdex"));
    ASSERT_TRUE(dso);
    dso->AddDexFileOffset(0x28);
    ASSERT_EQ(DSO_DEX_FILE, dso->type());
    const Symbol* symbol = dso->FindSymbol(0x6c77e);
    ASSERT_NE(symbol, nullptr);
    ASSERT_EQ(symbol->addr, static_cast<uint64_t>(0x6c77e));
    ASSERT_EQ(symbol->len, static_cast<uint64_t>(0x16));
    ASSERT_STREQ(symbol->DemangledName(),
                 "com.example.simpleperf.simpleperfexamplewithnative.MixActivity$1.run");
    ASSERT_EQ(0u, dso->MinVirtualAddress());
  }
#else
  GTEST_LOG_(INFO) << "This test only runs on linux because of libdexfile";
#endif  // defined(__linux__)
}
