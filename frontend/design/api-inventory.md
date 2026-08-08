# 后端 API 清单（前端对照表）

来源：`backend/app/api/routes.py`（FastAPI，启动后 Swagger 在 `/docs`）。
共 **31 个端点**。下表按教师工作流分组。原前端缺口（列表回读/改标/改分/否决/上传/报告回读/知识点浏览）已全部补齐并有测试覆盖。

---

## 1. 系统

| 方法 | 路径 | 请求 | 响应 | 前端用途 |
|---|---|---|---|---|
| GET | `/health` | — | `{status}` | 首屏探活，后端未启动时引导页 |

## 2. 初始化（向导页）

| 方法 | 路径 | 请求体 | 响应 | 前端用途 |
|---|---|---|---|---|
| POST | `/kb/upload` | **multipart**：`file`（知识库 YAML） | `{kb_version_id, status, version}` | 向导第 1 步：浏览器直接上传 |
| POST | `/kb/import` | `{yaml_path: str}` | 同上 | 部署预置场景（服务器路径） |
| POST | `/schools` | `{name}` | `{school_id}` | 向导第 2 步 |
| POST | `/schools/{id}/classes` | `{name, grade, subject="数学", student_aliases: [str]}` | `{class_id, student_ids[]}` | 建班 + 一次性录名单 |
| POST | `/classes/{id}/progress` | `{kp_codes: [str], taught_at: date}` | `{added: int}` | 教学进度写入；编码错误 400 |
| GET | `/classes/{id}/progress` | — | `{progress:[{code,name,taught_at}]}` | 进度回显（按教授日期排序） |
| GET | `/kb/kps` | — | `{kb_version_id, kps:[{code,name,chapter,grade}]}` | 向导进度勾选树 + 审核台闭集选择器 |

## 3. 全局列表（导航依赖）

| 方法 | 路径 | 请求 | 响应 | 前端用途 |
|---|---|---|---|---|
| GET | `/classes` | — | `{classes:[{class_id,name,grade,subject,school_id,student_count,exam_count}]}` | 班级选择器 |
| GET | `/classes/{id}/students` | — | `{students:[{student_id,name_or_alias,external_code}]}` | 学生选择器；**名单原序，无分数字段**（禁排名从接口层做起） |
| GET | `/exams?class_id=` | 可选过滤 | `{exams:[{exam_id,name,exam_date,type,source,question_count,response_counts,unreviewed_tags}]}` | 考试列表；`unreviewed_tags>0` 显示"去审核"角标 |
| GET | `/exams/{id}` | — | 模板详情 + 逐题标注（含 `source/confidence/reviewed`） | 审核台回显 |
| GET | `/exams/{id}/responses` | — | `{summary:{未采集,待审核,已提交}, responses:[{student_id,name_or_alias,status,response_id,total_score,low_confidence_count}]}` | 采集进度矩阵（名单原序） |

## 4. 考试录入

| 方法 | 路径 | 请求 | 响应 | 前端用途 |
|---|---|---|---|---|
| POST | `/exams/photo-template` | **multipart**：`file` + `class_id, name, exam_date, type` | `{exam_id, parse_job_id, questions, warnings[], next}` | 阶段A：照片→模板+LLM 标注（待审核）；warnings 必须展示 |
| POST | `/exams` | `ExamCreate`（questions 含 `kps:[{code,weight}]`） | `{exam_id, questions}` | 手工/Excel 建卷；**此路径标注自动视为已审核** |
| POST | `/exams/{id}/import-excel` | **multipart**：`file`（.xlsx） | `{imported, unmatched_students[], warnings[]}` | 未匹配学生要显著展示 |

## 5. 学生卷采集与审核

| 方法 | 路径 | 请求 | 响应 | 前端用途 |
|---|---|---|---|---|
| POST | `/exams/{id}/photo-response` | **multipart**：`file` + `student_id` | `{response_id, parse_job_id, warnings[], next}` | 阶段B；越界得分自动截断并告警 |
| POST | `/exams/{id}/manual` | `{student_id, scores: {题号: 得分}}` | `{response_id, total_score, status}` | 全量校验，任一题越界整体拒绝 |
| GET | `/exams/{id}/review-queue` | — | `{unreviewed_tags[], low_confidence_answers[]}`（`band`：强制人工<0.6 / 高亮提醒0.6~0.9） | 审核台数据源：异常式审核 |
| PATCH | `/template-questions/{qid}/tags` | `{kps:[{code,weight}], reviewer}` | `{question_id, kps, reviewed}` | **逐题改标**：闭集校验；改后 source=教师、视为已审核；已有已提交作答时 400 |
| PATCH | `/response-answers/{aid}` | `{score?, chosen_option?, reviewer}` | `{answer_id, score, chosen_option, total_score, status}` | **低置信得分修正**：仅限待审核；改后置信度置 1 退出队列，总分重算 |
| POST | `/exams/{id}/approve-tags` | query `reviewer` | `{approved}` | 标注闸门（全量批准） |
| POST | `/exams/{id}/commit` | — | `{committed_responses, evidence_events, skipped[]}` | 提交状态机 |

## 6. 分析（derive-on-read）

| 方法 | 路径 | 请求 | 响应 | 前端用途 |
|---|---|---|---|---|
| GET | `/students/{id}/mastery` | query `as_of` | `{mastery:[{code,name,mastery}]}` | 掌握度画像 |
| GET | `/students/{id}/weaknesses` | query `as_of` | `{weak:[{code,name,mastery,criterion,evidence_count,trajectory,stale,class_common}], gates}` | 薄弱卡片结构化数据源 |
| POST | `/students/{id}/attributions` | query `as_of` | `{attributions:[{kp,type,confidence,root_kp,prediction,status}]}` | 归因运行/刷新（诊断内部自动调用） |
| POST | `/attributions/{id}/override` | `{note, reviewer}` | `{attribution_id, status:"overridden", note}` | **教师否决**：引擎重跑永不复活被否决归因 |

## 7. 报告（Markdown 物化留档）

| 方法 | 路径 | 请求 | 响应 | 前端用途 |
|---|---|---|---|---|
| GET | `/classes/{id}/quality-report` | query `exam_id` + `narrative` | `{report_id, markdown}` | 班级质量分析；`narrative=true` 追加"AI 解读"段 |
| GET | `/students/{id}/diagnosis` | query `as_of`、`narrative` | `{report_id, markdown}` | 学生诊断单 |
| GET | `/reports` | 可选 `class_id` / `student_id` | `{reports:[{report_id,type,class_id,student_id,generated_at}]}` | 报告历史列表（按时间倒序） |
| GET | `/reports/{id}` | — | `{markdown, snapshot, ...}` | 报告回看（含导出时快照） |

---

## 前端联调注意事项

- **审核语义**：`reviewed=false` 只会出现在拍照路径的 LLM 草稿上；手工建卷标注自动已审核。
- **提交后锁定**：改标、改分在考试出现「已提交」作答后一律 400，界面应把入口置灰并解释（需以补录考试更正）。
- **所有教师修改留痕** `correction_log`（飞轮信号），前端无需感知。
- multipart 上传三处：photo-template / photo-response / import-excel / kb/upload，统一封装进度条与重试。
- AI 解读段识别标题：`## AI 解读（模型生成，数字以上文系统计算为准）`。
