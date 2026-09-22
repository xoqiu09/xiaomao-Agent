#!/bin/sh
# SwiftBar plugin: read-only Xiaomao status.
# Refresh interval comes from the filename (.1m). Does not scan, does not load a model.
#
# <xbar.title>小猫</xbar.title>
# <xbar.version>v0.1</xbar.version>
# <xbar.author>xiaomao</xbar.author>
# <xbar.desc>只读显示小猫扫描/日报状态，打开已有报告。过期则显示信息已过期。</xbar.desc>
# <xbar.dependencies>python3.11</xbar.dependencies>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.refreshOnOpen>true</swiftbar.refreshOnOpen>
# <swiftbar.runInBash>true</swiftbar.runInBash>

set -e
export PYTHONPATH="/Users/xiuqiu/WorkSpace/xiaomao-Agent/src"
export XIAOMAO_HOME="${XIAOMAO_HOME:-$HOME/Library/Application Support/Xiaomao}"
exec /Users/xiuqiu/.local/bin/python3.11 -m xiaomao.swiftbar
