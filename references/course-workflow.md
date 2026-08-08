# 课程工作流

## 目录

1. 模式识别
2. 阶段模型
3. 需求访谈与方案选择
4. Rolling Wave 计划
5. 状态转换
6. Checkpoint 格式
7. 恢复与长上下文
8. 最终验收

## 1. 模式识别

按以下优先级工作：

| 模式 | 判定 | 首要动作 |
|---|---|---|
| `resume` | `.course/course.json` 已存在 | 运行 `resume`，继续当前课程节 |
| `existing` | 项目已有源码但没有课程状态 | 先阅读规则、架构入口、manifest、测试和相似实现 |
| `new` | 用户要从零构建，目录没有目标实现 | 先访谈，再初始化课程与 Walking Skeleton |

不得覆盖已有 `.course`。若状态损坏，展示“修复状态、另建课程目录、停止”三个选项。

## 2. 阶段模型

| 阶段 | 目标 | 正式代码输出 | 退出条件 |
|---|---|---|---|
| 0. 需求访谈与方案选择 | 固定目标、约束、受众、技术栈和验收 | 无；此阶段不是正式课程节 | 用户确认方案和课程里程碑 |
| 1. Walking Skeleton | 建立可启动的最小架构和边界 | 独立骨架 Demo、主项目骨架、smoke test | 两条代码路径都能运行 |
| 2. 核心抽象与接口 | 固定关键数据、接口和依赖方向 | 最小抽象 Demo、主项目 contracts、contract tests | 复杂需求走查不破坏边界 |
| 3. 基础设施 | 提供当前项目真正需要的通用能力 | 每项能力的独立 Demo、主项目实现和测试 | 子系统不再自建重复基础能力 |
| 4. 子系统逐个实现 | 按依赖顺序完成业务能力 | 每个子系统的 Demo、完整主代码和测试 | 约定功能全部集成 |
| 5. 集成与本地质量 | 验证跨子系统流程、调试性和本地质量 | 集成场景、回归测试、必要诊断能力 | 核心用户流程稳定通过 |
| 6. 验收与复盘 | 证明课程和项目闭环 | 验收映射、最终架构文档、运行说明 | `validate --complete` 通过 |

基础设施清单不是固定购物清单。只有当前系统确实需要时才实现 pool、clock、random、event bus、scheduler、logging 或 configuration。

## 3. 需求访谈与方案选择

先读取环境中能发现的事实，再询问偏好。每轮最多提出三个会改变设计的决策。

访谈必须锁定：

- 用户要解决的问题和最终可观察结果。
- 学习者经验与希望掌握的能力。
- 新项目或已有项目。
- 语言、框架、平台和禁止事项。
- 本地运行与测试命令的目标形态。
- 功能范围和明确不做的内容。

在具体设计前提供 3–5 个架构方案。使用表格比较问题适配度、复杂度、性能或扩展成本、调试难度和推荐理由。用户选择后，把决定写入 checkpoint；不要只留在对话中。

用户确认课程后运行：

```text
python <skill-root>/scripts/course_state.py init <project-root> --mode <new|existing> --title <title>
```

随后填写 `.course/plan.md` 的最终目标、里程碑验收条件、当前课程节和下一课程节。

## 4. Rolling Wave 计划

一次固定完整里程碑和最终验收，不一次写完全部课程实现细节。

详细描述：

- 当前课程节：目标、决策点、Demo、主项目文件、验证命令和退出条件。
- 下一课程节：目标、依赖和为什么接在当前节之后。

后续阶段只保留目标、依赖、主要风险和验收结果。新发现改变路线时，先解释原计划为何失效，提供选项，记录新决策，再更新路线。

## 5. 状态转换

```text
planning
  -> active / lesson planned
  -> active / lesson in_progress
  -> blocked                         验证失败或缺少用户决策
  -> active / awaiting_confirmation  Demo、集成和测试均完成
  -> active / lesson complete        用户确认进入下一节
  -> complete                        最终验收通过
```

用户在 `awaiting_confirmation` 时追问，只回答当前节。用户选择“进入下一节”后，先用原课程节的完整验证信息建立 `complete` checkpoint，再启动下一节。

## 6. Checkpoint 格式

每次 checkpoint 提供完整当前快照，而不是含义不明的局部文本：

```json
{
  "course_status": "active",
  "phase_id": "1",
  "phase_status": "in_progress",
  "current_lesson": {
    "id": "01-walking-skeleton",
    "title": "可运行 Walking Skeleton",
    "status": "awaiting_confirmation",
    "demo_path": "learning-labs/course-a1b2c3d4/01-walking-skeleton",
    "doc_path": "docs/course/course-a1b2c3d4/lessons/01-walking-skeleton.md",
    "main_files": ["src/main.py"],
    "verification": [
      {
        "scope": "demo",
        "command": "python main.py",
        "status": "passed",
        "summary": "Demo 启动并输出预期状态"
      },
      {
        "scope": "project",
        "command": "python -m unittest",
        "status": "passed",
        "summary": "主项目 smoke test 通过"
      }
    ]
  },
  "next_lesson": {
    "id": "02-core-contracts",
    "title": "核心抽象与接口"
  },
  "summary": "Walking Skeleton 已完成，等待用户确认。",
  "decisions": [],
  "blockers": []
}
```

`awaiting_confirmation` 和 `complete` 必须同时包含通过的 `demo` 与 `project` 验证、讲解路径和至少一个主项目文件。

## 7. 恢复与长上下文

先运行 `resume`，再只读取：

1. `.course/plan.md` 的当前与下一课程节。
2. `.course/progress.md` 的最近 checkpoint。
3. 当前课程节讲解文档。
4. `course.json` 中列出的直接相关源码与测试。

不要为了“恢复完整”读取所有历史。关键理由应沉淀到 `decisions`，完整教学内容已在逐节文档中。

若 `progress.md` 超过约 400 行，将已完成阶段的旧记录移入 `.course/archive/phase-<id>.md`；保留当前阶段记录和归档索引，不删除历史。

## 8. 最终验收

先完成以下文件：

- 最终架构文档。
- 从 `assets/templates/acceptance-map.md` 创建的验收映射。
- 本地启动与测试说明。

最后一个 checkpoint 设置 `course_status` 为 `complete`，填入 `acceptance`：

```json
{
  "requirements_mapped": true,
  "requirements_map_path": "docs/acceptance-map.md",
  "architecture_current": true,
  "architecture_path": "docs/architecture.md",
  "start_command": {"command": "<实际命令>", "status": "passed"},
  "test_command": {"command": "<实际命令>", "status": "passed"},
  "excluded_launch_work": ["真实部署", "线上监控验证"]
}
```

运行 `validate --complete`。失败时保持当前阶段，修正实际项目或课程资产，不修改校验器来迁就失败结果。
