# RadioSim Tracer（仮称）のファーム（XIAO ESP32C6・ESP-IDF）。
# ビルドの手順は README.md。
cmake_minimum_required(VERSION 3.16)

# ファームの版＝git のコミット（刻印に入る＝TRACER_CONFIG.firmware_version）。
# 欄は char[16] なので「10 桁＋-dirty」で 16 文字に収める。作業ツリーが汚れて
# いたら -dirty を付ける＝コミットの名前だけでは、焼いたコードを後から再現できない。
execute_process(
    COMMAND git rev-parse --short=10 HEAD
    WORKING_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}
    OUTPUT_VARIABLE TRACER_COMMIT
    OUTPUT_STRIP_TRAILING_WHITESPACE
    RESULT_VARIABLE TRACER_GIT_RESULT)
if(NOT TRACER_GIT_RESULT EQUAL 0 OR TRACER_COMMIT STREQUAL "")
    # 版の分からないファームは刻印できない＝焼かせない。
    message(FATAL_ERROR "git のコミットを取れません（ファームの版は刻印の 1 項目です）")
endif()
execute_process(
    COMMAND git status --porcelain --untracked-files=no
    WORKING_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}
    OUTPUT_VARIABLE TRACER_DIRTY
    OUTPUT_STRIP_TRAILING_WHITESPACE)
if(NOT TRACER_DIRTY STREQUAL "")
    set(TRACER_COMMIT "${TRACER_COMMIT}-dirty")
endif()
set(PROJECT_VER "${TRACER_COMMIT}")

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
