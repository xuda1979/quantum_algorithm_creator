# 量子算法创造者（Quantum Algorithm Creator）

从形式化的可逆算术任务生成量子子程序，逐一核验输入输出、相位和辅助比特，再比较线路资源。Track A 提供已知构造。Track B 通过进化搜索改进同一功能的参数化线路。当前版本用 Python 标准库运行。

**本轮结果。** 复核了原有两种增量器，并在多数阈值、加权阈值和定值比较三类任务上找到五种经过验证的线路。在 `n=10` 的加权阈值任务上，低位优先构造相对同门集的固定宽度基线，将 Toffoli 数从 **227 降至 93**，逻辑深度从 **336 降至 146**，逻辑宽度为 **21**。原有 Top-Write 增量器与共享前缀构造相同，按已知构造交付。

## 运行与复现

需要 Python 3.10 或更高版本。在仓库根目录运行以下命令。

```bash
python -m qac.cli
python -m qac.cli audit --sizes 1,2,3,4,5,6,7,8 --json
python -m qac.cli search majority --seed 7 --holdout 6,7,8,10
python -m qac.cli search weighted_threshold --seed 7 --holdout 6,7,8,10 --json
python -m qac.cli search less_than --seed 7 --holdout 6,7,8,10
python -m qac.cli search weighted_threshold --no-evolution --seed 7
python -m pip install pytest
python -m pytest -q
```

`search` 默认在 `n=3,4,5` 上打分。`--batches` 限制搜索轮数，每轮最多评估两个候选。`--holdout` 指定与搜索尺寸互斥的验证集。JSON 记录父代、变异、候选参数、淘汰原因及逐尺寸资源。Python 接口 `run_agent()` 接收 `ProblemIR`，其中 `known_template` 或 `notes["library_key"]` 必须指定同一个受支持的任务。返回条目的 `candidate.build(n)` 可构造对应规模的 `Circuit`。

## 原有增量器的审计

两种旧实现均在 `n=1…8` 对所有输入实现 $|x,0^a\rangle\mapsto|x+1\bmod 2^n,0^a\rangle$，振幅为 $+1$。原基线把低位 $b_0$ 复制到辅助比特，并单独存储最高位需要的进位。Lean 去掉前一项，Top-Write 将后一项直接写到最高数据位。新对照 `reference:shared-prefix-increment` 从降序受控翻转恒等式出发，为重复出现的低位 AND 共享辅助位。最高位的 AND 可以直接作为翻转条件，后续按降序应用并清理其余前缀。

| `n=8` 构造 | 宽度 W | Toffoli 数 T | 门数 G | 深度 D | 审计结果 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 原前缀链 | 15 | 12 | 22 | 22 | 参考 |
| Lean Interleaved | 14 | 12 | 20 | 20 | 优于原前缀链，落后于共享前缀 |
| Top-Write | 13 | 11 | 18 | 18 | 与共享前缀线路一致 |
| 共享前缀 | 13 | 11 | 18 | 18 | 同规约专用参考 |
| CDKM 准备常数寄存器 | 17 | 16 | 50 | 42 | 通用模加法实例 |

从 `n≥3` 起，Lean 的 `W/T/G/D` 为 $2n-2,\ 2n-4,\ 3n-4,\ 3n-4$，Top-Write 为 $2n-3,\ 2n-5,\ 3n-6,\ 3n-6$。降序翻转时，各位读取的低位仍为原值，随后反算将辅助位清零。旧筛选池中的锯齿、循环进位、原地前瞻三个反例仍用于检验错误线路会被拒绝。文献中还有采用条件干净辅助位的增量器，以及不同宽度和深度取舍的常数加法构造。[Cuccaro 等](https://arxiv.org/abs/quant-ph/0410184) · [Khattar 与 Gidney](https://quantum-journal.org/papers/q-2025-05-21-1752/) · [Remaud 与 Vandaele](https://arxiv.org/abs/2501.16802)

## 进化搜索得到的线路

阈值线路实现 $|x,f,0^a\rangle\mapsto|x,f\oplus[\sum_i w_i x_i\ge\theta],0^a\rangle$。其中 $f$ 可以取任意输入值，各个基态的振幅均保持为 $+1$。因此该变换可用于叠加态中的相干条件标记。多数阈值取 $w_i=1$ 和 $\theta=\lceil n/2\rceil$，偶数个输入的平票满足阈值。加权阈值使用 $w_i=i+1$ 和 $\theta=\lfloor n(n+1)/4\rfloor$。

### 1. 多数阈值的偏置溢出标记

设总权重为 $S$，选最小的 $t$ 使 $2^t\ge\max(\theta,S-\theta+1)$。令低 $t$ 位累加器初值为 $2^t-\theta$，把标志位 $f$ 接在累加器高位。加入总和 $s$ 后，偏置和落在 $[0,2^{t+1})$，恰在 $s\ge\theta$ 时跨过 $2^t$，因此高位被异或翻转一次。清理阶段只对低位执行逆向模加法，再消除初始偏置，保留标志位上的结果。搜索还保留了一个直接比较的版本。它在 `n=10` 使用 63 个 Toffoli，宽度比偏置版多一个比特。

### 2. 加权阈值的低位优先累加

把常量权重拆成 $2^p$ 项，按位权从低到高累加。对每一步计算所有已处理项的最大可能和 $B$，然后把受控加法限制在能表示 $B$ 的位数内。高位一直为零，因此缩短的加法与整寄存器加法有相同作用。比较器写入标志后按逆序消去全部累加门。对相同位权的两个输入，还可以用 $a+b=(a\oplus b)+2ab$ 重新编码。`n=10` 的配对变体用更多 Toffoli 换取逻辑深度从 146 降至 136。

对采用末级直写、逐项累加的未配对构造，低位优先在此构造空间内最小化 Toffoli 数和门数。设相邻两项位权为 $2^p\le 2^q$，交换前的上界为 $B_0$。先加低位时，首步的寄存器位数 $m_p=\operatorname{bitlength}(B_0+2^p)$ 不大于先加高位时的 $m_q$。两项加完的位数 $m$ 与顺序无关。若将低位项提前，它的受控增量区间由 $m-p$ 缩短为 $m_p-p$，而高位项由 $m_q-q$ 扩大为 $m-q$。增量器的每位边际门成本随区间长度非递减。低位项缩短的位数至少与高位项扩大的位数相同，且在共同缩短或扩大的位数区间中边际成本更高或相同，因此总成本不会升高。逐对交换即可得到低位优先序。配对与逻辑深度仍由实测比较。

### 3. 定值比较的末级进位直写

有效数据为 $m=n-1$ 位无符号数，最高位是标志。任务为 $f\mathrel{\oplus}=[x<k]$，其中 $k=\min(n+1,2^{m}-1)$。比较链从 $k$ 的最低置位开始。对低 $j$ 位的比较结果 $D_j=[x\bmod 2^j\ge k\bmod 2^j]$，加入下一位时，按常量位选择 AND 或 OR。末级进位直接异或写入标志，随后反算前缀。该构造节省了最后一级的进位槽和一次反算。以下比较包含 IBM 的 `IntegerComparator`。[IBM Quantum 文档](https://qiskit.qotlabs.org/docs/api/qiskit/qiskit.circuit.library.IntegerComparator)

### 资源与复核范围

W 是逻辑比特数，T 是 Toffoli 数，G 是逻辑门数，D 是按依赖关系排程的全连接深度。表中各行均为 `n=10`。加权和多数任务的固定宽度对照已合并末级进位，并用同一门集实现比较和反算。

| 任务 | 原库 W/T/G/D | 固定宽度对照 W/T/G/D | 进化结果 W/T/G/D |
| --- | --- | --- | --- |
| 多数阈值，偏置溢出 | 19/126/217/213 | 17/105/171/168 | **16/72/122/120** |
| 多数阈值，按范围累加后比较 | 19/126/217/213 | 17/105/171/168 | **17/63/109/106** |
| 加权阈值，低位优先 | 23/262/433/387 | 21/227/359/336 | **21/93/159/146** |
| 加权阈值，低位优先配对 | 23/262/433/387 | 21/227/359/336 | **21/105/191/136** |
| 定值比较，末级直写 | 18/16/42/29 | — | **17/15/38/26** |

种子 7 在上述三类任务上分别评估了 **23、30、4** 个候选。种子 0 和 23 找到了相同的五种线路。多数与加权任务仅评估初始程序时均未获得认证的资源改进。搜索结束后，五个入选构造使用位平面模拟与独立 Kickmix 交叉核验，每个尺寸穷举所有数据输入和标志取值。搜索尺寸为 `3,4,5`，筛选留出尺寸为 `6,7,8,10`。冻结构造后又验证 `9,11,12,13`，连同最小尺寸 `1,2`，覆盖 `n=1…13`。

可通过以下 Python 代码复核冻结构造。

```python
from qac.novelty import certify_innovation
from qac.track_b import run_discovery

result = run_discovery(problem_key="weighted_threshold", seed=7,
                       holdout_sizes=(6, 7, 8, 10))
for entry in result.certified:
    receipt = certify_innovation(entry.candidate, extended_sizes=(9, 11, 12, 13))
    print(entry.candidate.id, receipt.correct, receipt.certified, receipt.extended_sizes)
```

### 独立 Qiskit 复核

Qiskit 2.6.2 在 `n=3,5,7` 上模拟随机复振幅叠加态。本轮五个构造与两个原增量器的最大振幅差均为 0。另一组实验将 `n=10` 电路统一编译为 `cx/rz/sx/x`，优化级别 3、种子 21、全连接。多数和加权的 SDK 基线由 `WeightedAdder`、`IntegerComparator` 与逆加法组成，复用已清零的工作位。定值比较的 SDK 基线直接使用 `IntegerComparator`。[IBM WeightedAdder 文档](https://quantum.cloud.ibm.com/docs/en/api/qiskit/dev/qiskit.circuit.library.WeightedAdder)

| 任务及候选 | Qiskit 基线宽度 / CX / 深度 | 候选宽度 / CX / 深度 |
| --- | --- | --- |
| 多数阈值，偏置溢出 | 19/1529/2676 | **16/426/713** |
| 加权阈值，低位优先 | 23/2716/4704 | **21/566/916** |
| 定值比较，末级直写 | 18/92/175 | **17/112/194** |

定值比较体现的是宽度取舍。SDK 基线多用一个比特，但 CX 更少、编译后的深度也更低。以上指标取自逻辑模拟和全连接编译，实际硬件的耦合、噪声与容错开销尚待测量。已有工作提出多对数深度、对数辅助比特的对称函数线路，本仓库的多数阈值结果是对指定基线的资源改进。加权线路对 `1…n` 权重的门数上界为 $O(n\log^2 n)$，多数阈值为 $O(n\log n)$。这两类构造的文献首创性仍需审查。[Zi、Nie 与 Sun](https://arxiv.org/abs/2404.06052)

```bash
python -m pip install qiskit==2.6.2
python -m benchmarks.qiskit_crosscheck
```

## 智能体怎样继续改进候选

1. **构造空间。** 从同规约初始程序出发，变异活动位宽、进位融合、项顺序、同权配对和阈值标记方式。每个候选是一套随 `n` 生成线路的规则。
2. **选择父代。** 精确评估器拒绝不满足功能、相位或辅助位契约的线路。MAP-Elites 存档按逻辑宽度和深度区间保留代表，后续候选由这些通过验证的父代变异而来。搜索使用种子控制提议顺序。
3. **交付。** 停止变异后才检查留出尺寸，随后按逐尺寸资源保留帕累托前沿。结构查重时对称控制位与互不依赖门的顺序按统一规则归一化。若在指定基线上找不到改进，返回同规约的已验证构造。

这一实现采用 OpenEvolve 的评估器反馈、精英存档和迭代变异思想。当前变异器使用可逆运算语法，搜索和评估使用本地 Python。[OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve)

`InnovationCertificate` 记录有限尺寸证据。候选在全部验证尺寸上各轴不劣于每条指定基线，并对每条基线至少取得一次严格改善，才能通过资源门槛。`novel` 字段表示相对比较池的线路结构差异，`literature_status` 单独记录文献审查状态。缺少同规约基线时可报告正确性。规模拟合用于警示显著增长，渐近界限来自构造分析。

## 已知构造与代码

| Track A 任务键 | 功能 |
| --- | --- |
| `increment`、`decrement`、`add_const`、`negate` | 模 $2^n$ 增量、减量、加常数和取负 |
| `bit_reversal`、`prefix_xor` | 比特反转和前缀奇偶变换 |
| `less_than`、`compare` | 定值比较和双寄存器比较 |
| `ctrl_increment`、`popcount` | 受控增量和相干位计数累加 |
| `weighted_threshold`、`majority` | 加权阈值和多数阈值 |

可运行模块包括 `qac/library.py`（已知构造）、`qac/arithmetic.py`（线路构造空间）、`qac/track_b.py`（搜索）、`qac/sandbox.py` 与 `qac/kickmix.py`（语义验证）、`qac/novelty.py`（基线证据）和 `qac/portfolio.py`（任务路由与结果）。通用问题形式化、LLM 变异器、Qualtran 导出与容错资源评估的长期设计见 [AGENT_DESIGN.md](AGENT_DESIGN.md)。
