"""规则知识库：规则号 → 所属类别、严重程度、产生原因、修复建议。

查找顺序：完整规则号 → 逐步缩短的前缀。换项目时一般不用改；想调整某条规则的
严重程度，在 profile 的 "severity_overrides" 里写 {"规则号": "high"} 即可。
"""
from __future__ import annotations

# ---------------------------------------------------------------- Ruff（Python）
# 前缀: (类别, 严重程度, 原因, 修复建议)
RUFF = {
    "invalid-syntax": ("01", "critical", "文件在目标 Python 版本下无法解析，导入即报错。", "按报错位置修正语法，或调整目标 Python 版本。"),
    "E9": ("01", "critical", "语法或缩进错误，模块无法导入。", "按报错位置修正语法。"),
    "F821": ("01", "high", "使用了未定义的名字，执行到这一行会抛 NameError。", "补上定义或 import；如果是拼写错误，改成正确的名字。"),
    "F822": ("01", "high", "__all__ 里导出了不存在的名字，`from x import *` 会失败。", "从 __all__ 删除或补上定义。"),
    "F823": ("01", "high", "局部变量在赋值前被引用，会抛 UnboundLocalError。", "先赋值再使用，或用 global / nonlocal 声明。"),
    "F5": ("01", "high", "字符串格式化的占位符与参数对不上，运行时抛异常或输出错误内容。", "让占位符与参数数量、名字一致。"),
    "F6": ("01", "high", "比较或解包写法有误（如 `is` 比较字面量、重复的字典键），结果与预期不符。", "按提示改成正确的比较 / 去掉重复键。"),
    "F7": ("01", "high", "控制流语句用在了不允许的位置。", "按提示调整语句位置。"),
    "F811": ("04", "medium", "同名定义被后面的定义覆盖，前一个永远不会被用到（也可能是误覆盖）。", "删除多余的那个；若两个都需要，改名。"),
    "F401": ("04", "low", "import 之后没有使用，增加加载时间和阅读负担。", "删除该 import；若是有意再导出，写进 __all__。"),
    "F841": ("04", "low", "局部变量赋值后没有使用，可能是遗留代码，也可能是忘了用。", "删除赋值；若右侧有副作用，只保留调用。"),
    "F842": ("04", "low", "变量只有类型标注，没有赋值也没有使用。", "删除。"),
    "F": ("01", "medium", "Pyflakes 检出的确定性问题（重定义、无效表达式等）。", "按提示修正。"),
    "B006": ("01", "high", "可变对象作默认参数，所有调用共用同一个对象，状态会串。", "默认值改 None，在函数体内再创建。"),
    "B008": ("01", "medium", "默认参数里调用函数，只在定义时求值一次。", "默认值改 None，在函数体内调用。"),
    "B023": ("01", "high", "闭包捕获了循环变量，实际执行时拿到的是最后一轮的值。", "用默认参数绑定当前值：`lambda x=x: ...`。"),
    "B012": ("01", "high", "finally 里的 return / break 会吞掉异常。", "把 return 移出 finally。"),
    "B904": ("01", "low", "except 里重新抛异常没带 `from`，原始异常链丢失，排查困难。", "写成 `raise NewError(...) from err`。"),
    "B": ("01", "medium", "flake8-bugbear 检出的易错写法。", "按提示改写。"),
    "PLE": ("01", "high", "Pylint 错误级问题，运行时大概率出错。", "按提示修正。"),
    "PLW0602": ("06", "low", "global 声明的变量没有在函数里赋值，声明多余。", "删除 global 声明。"),
    "PLW0603": ("06", "low", "用 global 修改模块级状态，调用顺序一变就容易出错，也难测试。", "把状态收进对象，或作为参数 / 返回值传递。"),
    "PLW2901": ("01", "low", "循环变量在循环体内被重新赋值，后面的代码容易读错值。", "换一个变量名。"),
    "PLW1510": ("01", "low", "subprocess.run 没写 check，命令失败会被静默忽略。", "明确写 `check=True` 或 `check=False`。"),
    "PLW": ("01", "low", "Pylint 警告级问题，写法容易出错。", "按提示改写。"),
    "ASYNC": ("01", "medium", "异步代码里有阻塞调用或缺少超时，会卡住事件循环。", "换成异步等价物，或加超时。"),
    "BLE": ("01", "low", "捕获了过宽的异常，真正的错误可能被吞掉。", "只捕获预期的异常类型，其余向上抛或记录后再抛。"),
    "RUF006": ("01", "medium", "asyncio 任务没有保存引用，可能在执行中被垃圾回收。", "把任务保存到变量或集合里。"),
    "RUF012": ("01", "medium", "类属性用了可变默认值，所有实例共用。", "改成在 __init__ 里创建，或用不可变类型。"),
    "RUF100": ("04", "low", "`# noqa` 对应的问题已不存在，注释多余。", "删除该 noqa。"),
    "RUF": ("06", "low", "Ruff 自有规则检出的可改进写法。", "按提示改写。"),
    "S102": ("02", "high", "exec 执行动态代码；内容若可被外部影响就是代码注入。", "改用明确的分支或查表；确实需要时限定输入来源。"),
    "S307": ("02", "high", "eval 执行动态表达式；内容若可被外部影响就是代码注入。", "改用 ast.literal_eval 或显式解析。"),
    "S602": ("02", "high", "subprocess 用了 shell=True，参数里有外部输入时可被注入命令。", "传参数列表、去掉 shell=True；必须用 shell 时用 shlex.quote。"),
    "S604": ("02", "high", "函数调用带 shell=True，存在命令注入风险。", "同上：传参数列表，不经过 shell。"),
    "S605": ("02", "high", "os.system / popen 经过 shell 执行，参数拼接可被注入。", "改用 subprocess.run([...])。"),
    "S608": ("02", "high", "用字符串拼接 / 格式化构造 SQL，变量来自外部时可被注入。", "改用参数化查询（`?` 占位符）；表名、列名只允许白名单内的值。"),
    "S301": ("02", "high", "pickle 反序列化不可信数据可执行任意代码。", "换 JSON；或只反序列化自己写出且有完整性校验的数据。"),
    "S506": ("02", "high", "yaml.load 不安全，可构造任意对象。", "改用 yaml.safe_load。"),
    "S501": ("02", "high", "HTTPS 请求关闭了证书校验，可被中间人攻击。", "去掉 verify=False。"),
    "S113": ("02", "medium", "HTTP 请求没有超时，对端不响应时线程永久挂起。", "加 timeout 参数。"),
    "S324": ("02", "medium", "使用了弱哈希（MD5 / SHA1）；用于安全目的时不够强。", "安全用途换 SHA-256；非安全用途加 usedforsecurity=False。"),
    "S105": ("02", "medium", "疑似硬编码的口令 / token（需人工确认，常见误报是字段名）。", "真实密钥移到环境变量或密钥文件；误报加行内说明。"),
    "S106": ("02", "medium", "疑似硬编码的口令参数（需人工确认）。", "同上。"),
    "S107": ("02", "medium", "疑似硬编码的口令默认值（需人工确认）。", "同上。"),
    "S108": ("02", "medium", "使用了固定的临时文件路径，多用户机器上可被抢占或读取。", "改用 tempfile 模块。"),
    "S103": ("02", "medium", "文件权限设置过宽。", "收紧到 0o600 / 0o700。"),
    "S310": ("02", "low", "urlopen 接受任意协议（含 file://）；URL 来自外部时有风险。", "URL 来自外部时先校验 scheme 为 http/https。"),
    "S110": ("02", "low", "try-except-pass 吞掉了所有异常，故障不留痕迹。", "至少记录日志，或缩小捕获的异常类型。"),
    "S112": ("02", "low", "try-except-continue 吞掉了异常。", "同上。"),
    "S603": ("02", "low", "subprocess 调用，需确认参数不含未经校验的外部输入。", "确认参数来源；外部输入先做白名单校验。"),
    "S607": ("02", "low", "按名字启动程序，依赖 PATH 查找，PATH 被篡改时会执行错误的程序。", "敏感环境用绝对路径。"),
    "S311": ("02", "low", "random 不是密码学安全的随机源。", "用于密钥 / token / nonce 时改用 secrets 模块；其他用途可忽略。"),
    "S": ("02", "medium", "flake8-bandit 检出的安全风险写法。", "按提示处理。"),
    "PERF401": ("03", "low", "循环里逐个 append，比列表推导慢，且多一次次的方法查找。", "改成列表推导或 list.extend。"),
    "PERF203": ("03", "low", "循环体内的 try/except 在 3.11 之前每轮都有额外开销。", "把 try 提到循环外，或确认目标版本 ≥ 3.11 后忽略。"),
    "PERF": ("03", "low", "Perflint 检出的低效写法（多余的转换、可用推导式的循环等）。", "按提示改写。"),
    "C4": ("03", "low", "多余的 list()/dict()/set() 包装或可用推导式的写法，多一次中间对象的创建。", "按提示改成字面量或推导式。"),
    "ERA": ("04", "low", "被注释掉的代码：不执行、也没人维护，版本控制里已有历史。", "删除；需要时从 git 历史找回。"),
    "PIE790": ("04", "low", "多余的 pass / `...`。", "删除。"),
    "C901": ("06", "medium", "函数圈复杂度过高（分支太多），难以读懂和测全。", "拆成几个职责单一的小函数；用提前返回、查表代替层层嵌套。"),
    "PLR0911": ("06", "low", "函数的 return 语句过多，出口分散。", "合并分支或拆函数。"),
    "PLR0912": ("06", "medium", "函数分支过多，难以测全。", "拆函数、查表分发。"),
    "PLR0913": ("06", "low", "函数参数过多，调用处容易传错位置。", "把相关参数收进 dataclass，或改成仅关键字参数。"),
    "PLR0915": ("06", "medium", "函数语句过多，职责不单一。", "按步骤拆成子函数。"),
    "PLR": ("06", "low", "Pylint 重构建议。", "按提示改写。"),
    "N": ("06", "low", "命名不符合 PEP 8 约定。", "按约定改名（注意同步调用处）。"),
    "SIM": ("06", "low", "可以更简单的写法。", "按提示简化。"),
    "UP": ("06", "low", "还在用旧版本 Python 的写法，目标版本已有更简洁的等价物。", "按提示升级写法。"),
    "RET": ("06", "low", "return 的写法不一致或有多余的 else / 赋值。", "按提示整理。"),
    "PIE": ("06", "low", "多余或可简化的写法。", "按提示改写。"),
    "FURB": ("06", "low", "有更直接的标准库写法。", "按提示改写。"),
    "E": ("06", "low", "不符合 pycodestyle 规范。", "按提示调整。"),
    "W": ("06", "low", "不符合 pycodestyle 规范。", "按提示调整。"),
}

RUFF_SELECT = ["F", "E4", "E7", "E9", "W6", "B", "S", "PERF", "C4", "C90", "N", "SIM", "UP", "PLE", "PLW",
               "PLR0911", "PLR0912", "PLR0913", "PLR0915", "PLR1704", "PLR1714", "PLR5501", "RUF", "ASYNC", "ERA",
               "RET", "PIE", "FURB"]
# RUF001-003：中文标点会被当成「易混淆字符」；S101：assert；E501：行宽；E702：项目自定的紧凑写法
# RUF100 不能开：项目里的 noqa 是按它自己的规则集写的，换一套规则后会被误判为多余
# SIM300：全大写的模块级变量会被当成常量而误报「尤达条件」；FURB157：Decimal("100") 改成 Decimal(100) 纯属写法偏好
RUFF_IGNORE = ["SIM300", "FURB157", "RUF100", "RUF001", "RUF002", "RUF003", "S101", "E501", "E702", "E402", "E741", "RET504", "SIM108",
               "UP015", "PLW0108"]
# 进入 fixes.patch 的自动修复：只用 Ruff 标为「安全」（不改变语义）的修复
RUFF_FIX_SELECT = ["F401", "F541", "F632", "F811", "PERF", "C4", "PIE790", "SIM", "PLR1714", "PLR5501",
                   "RET505", "RET506", "RET507", "RET508", "FURB", "B009", "B010"]

# ---------------------------------------------------------------- Pylint（补 Ruff 没有的）
PYLINT = {
    "E": ("01", "high", "Pylint 错误级问题：按类型推断，这行在运行时会出错（需人工确认推断是否准确）。", "按提示修正调用方式或名字。"),
    "W0101": ("04", "medium", "return / raise 之后的代码永远执行不到。", "删除，或检查上面的 return 是否写错了位置。"),
    "W0631": ("01", "high", "循环结束后使用循环变量；循环一次都没执行时变量未定义。", "循环前给变量赋初值，或把使用移进循环。"),
    "W0640": ("01", "high", "闭包捕获循环变量，执行时拿到的是最后一轮的值。", "用默认参数绑定当前值。"),
    "W0150": ("01", "high", "finally 里的 return 会吞掉异常。", "把 return 移出 finally。"),
    "W0109": ("01", "high", "字典字面量有重复的键，前面的值被覆盖。", "删除重复项。"),
    "W0106": ("01", "medium", "表达式的结果没有被使用，可能漏了赋值或调用。", "补上赋值 / 调用，或删除。"),
    "W0199": ("01", "high", "assert 的对象是元组，永远为真。", "去掉括号：`assert cond, msg`。"),
    "W3101": ("02", "medium", "HTTP 请求没有超时，对端不响应时永久挂起。", "加 timeout 参数。"),
    "W1514": ("10", "low", "open() 没指定 encoding，默认编码随操作系统而变（Windows 上不是 UTF-8）。", "写明 `encoding=\"utf-8\"`。"),
    "W": ("01", "low", "Pylint 警告。", "按提示改写。"),
    "R0401": ("05", "medium", "模块之间循环 import：加载顺序敏感，改动一处容易牵动一圈。", "把共用部分下移到被双方依赖的模块，或把 import 改成函数内延迟导入。"),
    "R0902": ("06", "low", "类的实例属性过多，职责可能不单一。", "把相关属性拆到子对象。"),
    "R0904": ("06", "low", "类的公开方法过多。", "按职责拆类。"),
    "R0914": ("06", "low", "函数局部变量过多，说明函数做了太多事。", "拆成子函数。"),
    "R1702": ("06", "medium", "嵌套层数过深，难以读懂。", "用提前返回 / continue 展平，或抽出内层为函数。"),
    "R0916": ("06", "low", "if 里的布尔表达式过多。", "拆成有名字的中间变量。"),
    "C0302": ("06", "low", "单个模块行数过多，难以导航和评审。", "按职责拆成多个模块。"),
    "R": ("06", "low", "Pylint 重构建议。", "按提示改写。"),
    "C": ("06", "low", "Pylint 规范建议。", "按提示改写。"),
}
PYLINT_ENABLE = ["E", "W0101", "W0631", "W0640", "W0150", "W0109", "W0106", "W0199", "W3101", "W1514",
                 "R0401", "R0902", "R0904", "R0914", "R1702", "R0916", "C0302"]
# 类型推断类的检查在动态代码上误报多，默认关闭
PYLINT_DISABLE = ["E1101", "E0611", "E1136", "E1137", "E1133", "E1135", "E1120", "E1123", "E0401", "E0601", "E0606"]

# ---------------------------------------------------------------- Clippy / rustc（Rust）
CLIPPY = {
    "clippy::unwrap_used": ("01", "medium", "非测试代码里的 unwrap()：值为 None / Err 时线程直接 panic，长跑进程会因此退出。", "用 `?` 向上传错误，或 match / unwrap_or 处理；确实不可能失败时改 expect(\"原因\") 写明理由。"),
    "clippy::panic": ("01", "medium", "非测试代码里显式 panic!，触发即终止线程。", "改成返回 Err，由上层决定停机还是恢复。"),
    "clippy::todo": ("01", "high", "留有 todo!()，执行到就 panic。", "补上实现。"),
    "clippy::unimplemented": ("01", "high", "留有 unimplemented!()，执行到就 panic。", "补上实现或返回明确的错误。"),
    "clippy::dbg_macro": ("06", "low", "遗留的 dbg! 调试宏，会往 stderr 打印。", "删除。"),
    "clippy::cast_possible_truncation": ("01", "low", "`as` 转成更窄的整数 / 浮点，超范围时静默截断，数值出错不报错。", "用 try_from 并处理失败；确认范围安全时就地 #[allow] 并写明理由。"),
    "clippy::cast_sign_loss": ("01", "low", "有符号转无符号，负数会变成很大的正数。", "用 try_from，或先判断非负。"),
    "clippy::cast_possible_wrap": ("01", "low", "无符号转有符号，大值会变成负数。", "用 try_from。"),
    "clippy::await_holding_lock": ("01", "high", "持有同步锁跨越 .await，可能死锁或长时间阻塞其他任务。", "在 await 之前释放锁（缩小作用域），或换异步锁。"),
    "clippy::redundant_clone": ("03", "low", "多余的 clone()：值之后不再使用，白做一次复制 / 分配。", "去掉 clone，直接移动。"),
    "clippy::needless_pass_by_value": ("03", "low", "参数按值传入却只读，调用方被迫 clone 或交出所有权。", "改成借用 `&T`。"),
    "clippy::inefficient_to_string": ("03", "low", "对 &&str 调 to_string 走的是较慢的 Display 路径。", "先解引用：`(*s).to_string()`。"),
    "clippy::large_stack_arrays": ("03", "medium", "栈上分配大数组，可能栈溢出。", "改用 Vec / Box。"),
    "clippy::large_futures": ("03", "medium", "Future 体积过大，每次移动都要复制大量内存。", "用 Box::pin 包装。"),
    "clippy::large_enum_variant": ("03", "low", "枚举某个成员远大于其他成员，所有值都按最大的占内存。", "把大成员装进 Box。"),
    "clippy::cognitive_complexity": ("06", "medium", "函数认知复杂度过高，难以读懂和测全。", "按步骤拆成子函数。"),
    "clippy::too_many_lines": ("06", "low", "函数过长。", "按步骤拆成子函数。"),
    "clippy::too_many_arguments": ("06", "low", "函数参数过多。", "把相关参数收进结构体。"),
    "dead_code": ("04", "low", "定义了但从未使用的函数 / 字段 / 类型。", "删除；仅测试使用的加 #[cfg(test)]。"),
    "unused_imports": ("04", "low", "use 之后没有使用。", "删除该 use。"),
    "unused_variables": ("04", "low", "变量未使用。", "删除，或加下划线前缀表示有意忽略。"),
    "unused_mut": ("04", "low", "声明了 mut 但没有修改。", "去掉 mut。"),
    "unused": ("04", "low", "未使用的项。", "删除。"),
    "deprecated": ("10", "medium", "用了已弃用的 API，后续版本会移除。", "换成提示里的替代 API。"),
    "clippy::": ("06", "low", "Clippy 默认规则检出的可改进写法。", "按 Clippy 给出的 help 修改。"),
    "": ("01", "medium", "编译器警告。", "按编译器提示修改。"),
}
# 默认规则之外额外打开的规则（只统计非测试代码，见 linters.run_clippy）
CLIPPY_EXTRA = ["clippy::unwrap_used", "clippy::panic", "clippy::todo", "clippy::unimplemented", "clippy::dbg_macro",
                "clippy::cast_possible_truncation", "clippy::cast_sign_loss", "clippy::cast_possible_wrap",
                "clippy::redundant_clone", "clippy::needless_pass_by_value", "clippy::inefficient_to_string",
                "clippy::large_stack_arrays", "clippy::large_futures", "clippy::cognitive_complexity",
                "clippy::too_many_lines"]
CLIPPY_PERF = {"box_collection", "boxed_local", "cmp_owned", "collapsible_str_replace", "expect_fun_call",
               "extend_with_drain", "format_collect", "format_in_format_args", "iter_overeager_cloned",
               "large_const_arrays", "manual_memcpy", "manual_retain", "manual_str_repeat", "map_entry",
               "missing_spin_loop", "needless_collect", "redundant_allocation", "regex_creation_in_loops",
               "result_large_err", "single_char_pattern", "slow_vector_initialization", "to_string_in_format_args",
               "unnecessary_to_owned", "useless_vec", "vec_init_then_push", "waiting_on_new_line", "or_fun_call",
               "iter_nth", "drain_collect", "needless_bitwise_bool"}


def lookup(table: dict, rule: str) -> tuple[str, str, str, str] | None:
    r = rule or ""
    while True:
        if r in table:
            return table[r]
        if not r:
            return None
        r = r[:-1]


def clippy_lookup(rule: str) -> tuple[str, str, str, str]:
    if rule in CLIPPY:
        return CLIPPY[rule]
    short = rule.removeprefix("clippy::")
    if rule.startswith("clippy::") and short in CLIPPY_PERF:
        return ("03", "low", "Clippy 性能规则：这种写法有多余的分配、复制或更慢的路径。", "按 Clippy 给出的 help 修改。")
    if rule.startswith("unused"):
        return CLIPPY["unused"]
    if rule.startswith("clippy::"):
        return CLIPPY["clippy::"]
    return CLIPPY[""]
