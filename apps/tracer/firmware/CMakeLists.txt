# RadioSim Tracer（仮称）のファーム（XIAO ESP32C6・ESP-IDF）。
# ビルドの手順は README.md。
cmake_minimum_required(VERSION 3.16)

# ファームの版（git のコミット）は、ここ（構成時）では取らない＝version.cmake が
# ビルドのたびに取り、main/tracer_version.h に書く（B-256）。
# PROJECT_VER（アプリ記述子の版）は構成時にしか決まらず古くなり得るので、刻印には
# 使わない。版と取り違えられないよう、コミットに見えない固定の値にしておく
# （既定のままだと ESP-IDF が構成時の git describe を入れ、同じ取り違えが起きる）。
set(PROJECT_VER "see-config")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# ESP-IDF の版を固定する。刻印に残るのはファームのコミットだけなので、別の版で
# ビルドできてしまうと、同じコミットのファームが別の無線スタックで動いていても
# 記録から見分けられない。版を上げるときは、ここと README を直してコミットする。
set(TRACER_IDF_VERSION "6.1")
if(NOT "${IDF_VERSION_MAJOR}.${IDF_VERSION_MINOR}" STREQUAL TRACER_IDF_VERSION)
    message(FATAL_ERROR
        "ESP-IDF v${TRACER_IDF_VERSION} でビルドしてください"
        "（いまは v${IDF_VERSION_MAJOR}.${IDF_VERSION_MINOR}.${IDF_VERSION_PATCH}）")
endif()

project(radiosim_tracer)
