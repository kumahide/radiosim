# sdkconfig.defaults の各行が、ビルドに使う sdkconfig にそのまま在るかを照合し、
# 食い違えばビルドを止める。**ビルドのたびに**走らせる（main/CMakeLists.txt の
# radiosim_field_version が版の刻印より先に呼ぶ）。
#
#   cmake -DDEFAULTS=<sdkconfig.defaults> -DSDKCONFIG=<sdkconfig> -P sdkconfig_check.cmake
#
# ⚠️ 刻印に残る版はコミットだけで、git 管理外の sdkconfig は `-dirty` の判定にも
# 入らない。既にある sdkconfig の値は sdkconfig.defaults より優先されるので、既定を
# 直してコミットしても古い設定のまま焼け、それでも新しいコミットを名乗る（B-260）。
# 照合するのは既定に書いた行だけ＝menuconfig で既定に無い項目を変えた分は見えない。
if(NOT DEFAULTS OR NOT SDKCONFIG)
    message(FATAL_ERROR "DEFAULTS と SDKCONFIG を渡してください")
endif()
if(NOT EXISTS ${SDKCONFIG})
    message(FATAL_ERROR "sdkconfig がありません: ${SDKCONFIG}")
endif()

file(STRINGS ${DEFAULTS} wanted_lines)
file(STRINGS ${SDKCONFIG} actual_lines)
set(actual "")
foreach(line IN LISTS actual_lines)
    string(STRIP "${line}" line)
    list(APPEND actual "${line}")
endforeach()

set(mismatches "")
foreach(line IN LISTS wanted_lines)
    string(STRIP "${line}" line)
    # 設定の行は「CONFIG_X=値」と「# CONFIG_X is not set」の 2 形。ほかは注釈。
    if(line MATCHES "^(CONFIG_[A-Za-z0-9_]+)=")
        set(name "${CMAKE_MATCH_1}")
    elseif(line MATCHES "^# (CONFIG_[A-Za-z0-9_]+) is not set$")
        set(name "${CMAKE_MATCH_1}")
    else()
        continue()
    endif()
    list(FIND actual "${line}" found)
    if(found EQUAL -1)
        # sdkconfig 側の今の値を添える（無ければ、依存が満たされず項目ごと消えている）。
        set(current "（sdkconfig に項目が無い）")
        foreach(candidate IN LISTS actual)
            if(candidate MATCHES "^${name}=" OR candidate STREQUAL "# ${name} is not set")
                set(current "${candidate}")
                break()
            endif()
        endforeach()
        list(APPEND mismatches "  既定: ${line}\n  実際: ${current}")
    endif()
endforeach()

if(mismatches)
    list(JOIN mismatches "\n" report)
    message(FATAL_ERROR
        "sdkconfig が sdkconfig.defaults と食い違っています（このまま焼くと、"
        "ファームの版と中身の設定が一致しません）。\n${report}\n"
        "${SDKCONFIG} を消してからビルドし直してください。")
endif()
