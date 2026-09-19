# ファームの版（刻印に入る＝TRACER_CONFIG.firmware_version）を git から取り、
# ヘッダに書く。**ビルドのたびに**走らせる（main/CMakeLists.txt の tracer_version）。
#
#   cmake -DSRC_DIR=<git の作業ツリーの中> -DOUT=<tracer_version.h> -P version.cmake
#
# ⚠️ 構成時（execute_process を CMakeLists.txt に直に書く形）に取ってはいけない＝
# 既存の build フォルダへ `idf.py build` すると、ソースは作り直されるのに版は前回の
# 構成時のまま残る（B-256＝中身は新しいコミットなのに古いコミットを名乗った）。
#
# 欄は char[16] なので「10 桁＋-dirty」で 16 文字に収める。作業ツリーが汚れて
# いたら -dirty を付ける＝コミットの名前だけでは、焼いたコードを後から再現できない。
if(NOT SRC_DIR OR NOT OUT)
    message(FATAL_ERROR "SRC_DIR と OUT を渡してください")
endif()

execute_process(
    COMMAND git rev-parse --short=10 HEAD
    WORKING_DIRECTORY ${SRC_DIR}
    OUTPUT_VARIABLE TRACER_COMMIT
    OUTPUT_STRIP_TRAILING_WHITESPACE
    RESULT_VARIABLE TRACER_GIT_RESULT)
if(NOT TRACER_GIT_RESULT EQUAL 0 OR TRACER_COMMIT STREQUAL "")
    # 版の分からないファームは刻印できない＝焼かせない。
    message(FATAL_ERROR "git のコミットを取れません（ファームの版は刻印の 1 項目です）")
endif()
execute_process(
    COMMAND git status --porcelain --untracked-files=no
    WORKING_DIRECTORY ${SRC_DIR}
    OUTPUT_VARIABLE TRACER_DIRTY
    OUTPUT_STRIP_TRAILING_WHITESPACE
    RESULT_VARIABLE TRACER_GIT_RESULT)
if(NOT TRACER_GIT_RESULT EQUAL 0)
    message(FATAL_ERROR "git status に失敗しました（汚れているかを判定できません）")
endif()
if(NOT TRACER_DIRTY STREQUAL "")
    set(TRACER_COMMIT "${TRACER_COMMIT}-dirty")
endif()

set(content "/* 生成物（version.cmake がビルドのたびに書く）。手で編集しない。 */\n#define TRACER_FIRMWARE_VERSION \"${TRACER_COMMIT}\"\n")
# 中身が同じなら書かない＝版が変わらないビルドで main.c を作り直さない。
if(EXISTS ${OUT})
    file(READ ${OUT} previous)
else()
    set(previous "")
endif()
if(NOT previous STREQUAL content)
    file(WRITE ${OUT} "${content}")
endif()
