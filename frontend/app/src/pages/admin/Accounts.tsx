import {
  BookOpen,
  ChalkboardTeacher,
  CheckCircle,
  Plus,
  UserGear,
  X,
} from "@phosphor-icons/react";
import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Modal,
  Page,
  PageHeader,
  Select,
  Skeleton,
} from "../../components/ui";
import {
  createTeacher,
  grantTeacherClasses,
  listClasses,
  listTeachers,
  setHomeroom,
  setSubjectScopes,
  setTeacherKbEditor,
  type TeacherAccountRow,
} from "../../lib/api";
import { useAsync } from "../../lib/hooks";
import { ACCENTS } from "../../lib/theme";

/**
 * 账号管理（frontend-ends-design §C 校务台 + rbac-scopes-design §7）：admin-only。
 *
 * 「教师账号」：列全部教师（name/username/admin/kb_editor/已授班级/学科×年级
 * 授权/班主任班级），新建教师，行内授权班级（可带科任学科）+ 知识库编辑权 +
 * 学科管理员授权（subject×grade，覆盖式）。
 * 「班级」：班主任指派（一班一人；班主任自动获本班全科视野与学生账号开通权）。
 * 学生自服务账号在「班级 → 学生」行内开通（见 Students 页 §D），本页只作引导说明。
 */

interface CreateForm {
  name: string;
  username: string;
  password: string;
  admin: boolean;
  kb_editor: boolean;
}

interface ScopeDraft {
  subject: string;
  grade: number;
}

const EMPTY_FORM: CreateForm = { name: "", username: "", password: "", admin: false, kb_editor: false };

const COMMON_SUBJECTS = ["数学", "语文", "英语", "物理", "化学", "生物", "历史", "地理", "道德与法治"];

export default function Accounts() {
  const teachers = useAsync(() => listTeachers(), []);
  const classes = useAsync(() => listClasses(), []);
  const classRows = classes.data?.classes ?? [];

  const [tab, setTab] = useState<"teachers" | "classes">("teachers");

  const schoolId = classRows[0]?.school_id ?? null;
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<CreateForm>(EMPTY_FORM);
  const [createErr, setCreateErr] = useState<string | null>(null);
  const [createBusy, setCreateBusy] = useState(false);

  // 行内授权班级：编辑中的教师 + 勾选
  const [grantFor, setGrantFor] = useState<TeacherAccountRow | null>(null);
  const [grantSel, setGrantSel] = useState<Set<number>>(new Set());
  const [grantBusy, setGrantBusy] = useState(false);
  const [grantErr, setGrantErr] = useState<string | null>(null);

  // 知识库编辑权开关（两层写权）：行内直接切换
  const [kbBusy, setKbBusy] = useState<number | null>(null);
  const [kbErr, setKbErr] = useState<string | null>(null);

  // 学科管理员授权（学科×年级，覆盖式；rbac-scopes-design §7）
  const [scopeFor, setScopeFor] = useState<TeacherAccountRow | null>(null);
  const [scopeDraft, setScopeDraft] = useState<ScopeDraft[]>([]);
  const [scopeSubject, setScopeSubject] = useState("");
  const [scopeGrade, setScopeGrade] = useState(7);
  const [scopeBusy, setScopeBusy] = useState(false);
  const [scopeErr, setScopeErr] = useState<string | null>(null);

  // 班主任指派（班级页签）
  const [hrBusy, setHrBusy] = useState<number | null>(null);
  const [hrErr, setHrErr] = useState<string | null>(null);

  const openGrant = (t: TeacherAccountRow) => {
    setGrantFor(t);
    setGrantSel(new Set(t.classes.map((c) => c.class_id)));
    setGrantErr(null);
  };

  const openScopes = (t: TeacherAccountRow) => {
    setScopeFor(t);
    setScopeDraft(t.subject_scopes.map((s) => ({ ...s })));
    setScopeSubject("");
    setScopeGrade(7);
    setScopeErr(null);
  };

  const doCreate = async () => {
    if (!schoolId) return;
    setCreateBusy(true);
    setCreateErr(null);
    try {
      await createTeacher({ ...createForm, school_id: schoolId });
      setShowCreate(false);
      setCreateForm(EMPTY_FORM);
      teachers.reload();
    } catch (e) {
      setCreateErr(e instanceof Error ? e.message : "创建失败");
    } finally {
      setCreateBusy(false);
    }
  };

  const doGrant = async () => {
    if (!grantFor) return;
    setGrantBusy(true);
    setGrantErr(null);
    try {
      await grantTeacherClasses(grantFor.teacher_id, [...grantSel]);
      setGrantFor(null);
      teachers.reload();
    } catch (e) {
      setGrantErr(e instanceof Error ? e.message : "授权失败");
    } finally {
      setGrantBusy(false);
    }
  };

  const doKbEditor = async (t: TeacherAccountRow, next: boolean) => {
    setKbBusy(t.teacher_id);
    setKbErr(null);
    try {
      await setTeacherKbEditor(t.teacher_id, next);
      teachers.reload();
    } catch (e) {
      setKbErr(e instanceof Error ? e.message : "切换失败");
    } finally {
      setKbBusy(null);
    }
  };

  const doSaveScopes = async () => {
    if (!scopeFor) return;
    setScopeBusy(true);
    setScopeErr(null);
    try {
      await setSubjectScopes(scopeFor.teacher_id, scopeDraft);
      setScopeFor(null);
      teachers.reload();
    } catch (e) {
      setScopeErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setScopeBusy(false);
    }
  };

  const doHomeroom = async (classId: number, teacherId: number | null) => {
    setHrBusy(classId);
    setHrErr(null);
    try {
      await setHomeroom(classId, teacherId);
      classes.reload();
      teachers.reload();
    } catch (e) {
      setHrErr(e instanceof Error ? e.message : "指派失败");
    } finally {
      setHrBusy(null);
    }
  };

  const rows = teachers.data?.teachers ?? [];
  const teacherOptions = rows.map((t) => ({ id: t.teacher_id, name: t.name }));
  const toggle = (id: number) =>
    setGrantSel((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const tabBtn = (key: "teachers" | "classes", label: string) => (
    <button
      onClick={() => setTab(key)}
      className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
        tab === key ? "bg-accent-soft font-semibold text-accent-deep" : "text-ink-soft hover:text-accent"
      }`}
    >
      {label}
    </button>
  );

  return (
    <Page accent={ACCENTS.dashboard}>
      <PageHeader
        title="账号管理"
        desc="教师账号、学科×年级授权与班主任指派（学生自服务账号在对应班级的学生列表开通）"
        actions={
          tab === "teachers" && schoolId ? (
            <Button onClick={() => { setCreateForm(EMPTY_FORM); setCreateErr(null); setShowCreate(true); }}>
              <Plus size={15} /> 新建教师
            </Button>
          ) : undefined
        }
      />

      <div className="mb-4 flex gap-1 rounded-xl bg-surface-2/60 p-1 w-fit">
        {tabBtn("teachers", "教师账号")}
        {tabBtn("classes", "班级 / 班主任")}
      </div>

      {tab === "teachers" && (
        <>
          {teachers.loading && <Skeleton rows={4} />}
          {teachers.error && <ErrorState message={teachers.error} onRetry={teachers.reload} />}

          {!teachers.loading && !teachers.error && rows.length === 0 && (
            <Card>
              <EmptyState
                title="还没有教师账号"
                hint={
                  schoolId
                    ? "新建第一个教师账号后，系统进入安全模式（其余账号需登录）。"
                    : "暂无班级/学校，请先通过初始化向导建校建班，再在此开通教师账号。"
                }
              />
            </Card>
          )}

          {rows.length > 0 && (
            <Card className="divide-y divide-line">
              {rows.map((t) => (
                <div key={t.teacher_id} className="flex flex-wrap items-center gap-3 px-5 py-3.5">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-accent-deep">
                    <ChalkboardTeacher size={16} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-2 truncate text-sm font-medium">
                      {t.name}
                      {t.admin && <Badge tone="accent">管理员</Badge>}
                      {t.kb_editor && <Badge tone="neutral">知识库编辑</Badge>}
                      {t.homeroom_class_ids.length > 0 && (
                        <Badge tone="neutral">
                          班主任 · {t.homeroom_class_ids.map((cid) => classRows.find((c) => c.class_id === cid)?.name ?? `班${cid}`).join("、")}
                        </Badge>
                      )}
                    </p>
                    <p className="flex flex-wrap items-center gap-2 text-xs text-ink-faint">
                      <span className="font-mono">{t.username ?? "（未设登录名）"}</span>
                      <span>
                        已授班级 {t.classes.length}：
                        {t.classes.length > 0
                          ? t.classes.map((c) => c.name).join("、")
                          : "未授权（admin 全校可见不受限）"}
                      </span>
                      {t.subject_scopes.length > 0 && (
                        <span>
                          学科管理：
                          {t.subject_scopes.map((s) => `${s.subject}·${s.grade}年级`).join("、")}
                        </span>
                      )}
                    </p>
                  </div>
                  {!t.admin && (
                    <>
                      <Button
                        variant={t.kb_editor ? "primary" : "secondary"}
                        onClick={() => doKbEditor(t, !t.kb_editor)}
                        disabled={kbBusy === t.teacher_id}
                        title="知识库内容编辑权：录知识点/关系、fork 草稿版本；范围授权见「学科授权」"
                      >
                        <BookOpen size={14} />
                        {kbBusy === t.teacher_id ? "切换中…" : t.kb_editor ? "知识库编辑 · 已授权" : "知识库编辑"}
                      </Button>
                      <Button
                        variant={t.subject_scopes.length > 0 ? "primary" : "secondary"}
                        onClick={() => openScopes(t)}
                        title="学科管理员授权（学科×年级）：该范围内知识库内容写权 + 启用版本的治理权"
                      >
                        <UserGear size={14} /> 学科授权
                      </Button>
                    </>
                  )}
                  <Button variant="secondary" onClick={() => openGrant(t)}>
                    <UserGear size={14} /> 授权班级
                  </Button>
                </div>
              ))}
            </Card>
          )}

          {kbErr && <p className="mt-2 text-xs text-danger">{kbErr}</p>}
        </>
      )}

      {tab === "classes" && (
        <>
          {classes.loading && <Skeleton rows={3} />}
          {classes.error && <ErrorState message={classes.error} onRetry={classes.reload} />}
          {!classes.loading && !classes.error && classRows.length === 0 && (
            <Card>
              <EmptyState title="暂无班级" hint="先通过初始化向导建班。" />
            </Card>
          )}
          {classRows.length > 0 && (
            <Card className="divide-y divide-line">
              {classRows.map((c) => (
                <div key={c.class_id} className="flex flex-wrap items-center gap-3 px-5 py-3.5">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">
                      {c.name}
                      <span className="ml-2 text-xs font-normal text-ink-faint">
                        {c.grade} 年级 · 默认学科 {c.subject} · {c.student_count} 名学生
                      </span>
                    </p>
                    {c.homeroom_teacher_name && (
                      <p className="text-xs text-ink-faint">
                        班主任 {c.homeroom_teacher_name}（自动获本班全科视野 + 学生账号开通权）
                      </p>
                    )}
                  </div>
                  <label className="flex items-center gap-2 text-xs text-ink-soft">
                    班主任
                    <Select
                      size="sm"
                      value={c.homeroom_teacher_id ?? ""}
                      disabled={hrBusy === c.class_id}
                      onChange={(e) => doHomeroom(c.class_id, e.target.value ? Number(e.target.value) : null)}
                      className="w-36"
                    >
                      <option value="">（未指派）</option>
                      {teacherOptions.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name}
                        </option>
                      ))}
                    </Select>
                  </label>
                </div>
              ))}
            </Card>
          )}
          {hrErr && <p className="mt-2 text-xs text-danger">{hrErr}</p>}
        </>
      )}

      {/* 新建教师 */}
      <Modal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        title="新建教师账号"
        footer={
          <>
            <Button variant="ghost" onClick={() => setShowCreate(false)} disabled={createBusy}>
              取消
            </Button>
            <Button
              variant="primary"
              onClick={doCreate}
              disabled={createBusy || !createForm.name.trim() || !createForm.username.trim() || createForm.password.length < 6}
            >
              {createBusy ? "创建中…" : "创建"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="姓名">
            <Input value={createForm.name} onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })} autoFocus />
          </Field>
          <Field label="登录名（2–64 字符，全校唯一）">
            <Input value={createForm.username} onChange={(e) => setCreateForm({ ...createForm, username: e.target.value })} />
          </Field>
          <Field label="初始口令（≥6 位）" hint="建号后立即进入安全模式；口令可请管理员后续重置。">
            <Input type="password" value={createForm.password} onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })} />
          </Field>
          <label className="flex items-center gap-2 text-sm text-ink-soft">
            <input
              type="checkbox"
              checked={createForm.admin}
              onChange={(e) => setCreateForm({ ...createForm, admin: e.target.checked })}
              className="accent-accent"
            />
            设为管理员（全校可见 + 校务台）
          </label>
          <label className="flex items-center gap-2 text-sm text-ink-soft">
            <input
              type="checkbox"
              checked={createForm.kb_editor}
              onChange={(e) => setCreateForm({ ...createForm, kb_editor: e.target.checked })}
              className="accent-accent"
            />
            授予知识库编辑权（全校内容层；按学科管理请用「学科授权」）
          </label>
          {createErr && <p className="text-xs text-danger">{createErr}</p>}
        </div>
      </Modal>

      {/* 授权班级 */}
      <Modal
        open={grantFor !== null}
        onClose={() => setGrantFor(null)}
        title={grantFor ? `授权班级 · ${grantFor.name}` : "授权班级"}
        footer={
          <>
            <Button variant="ghost" onClick={() => setGrantFor(null)} disabled={grantBusy}>
              取消
            </Button>
            <Button variant="primary" onClick={doGrant} disabled={grantBusy}>
              {grantBusy ? "保存中…" : "保存授权"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-xs text-ink-faint">
            admin 全校可见不受此限制；普通教师仅能访问勾选班级。勾选即覆盖该教师的全部授权。
            需要限制教师只看某学科考试时，用「学科授权」或后续的科任学科绑定。
          </p>
          {classes.error && <ErrorState message={classes.error} onRetry={classes.reload} />}
          {classRows.length === 0 ? (
            <EmptyState title="暂无班级" hint="先通过初始化向导建班。" />
          ) : (
            <ul className="grid gap-1.5 sm:grid-cols-2">
              {classRows.map((c) => {
                const on = grantSel.has(c.class_id);
                return (
                  <li key={c.class_id}>
                    <button
                      onClick={() => toggle(c.class_id)}
                      className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-sm text-left transition-colors ${
                        on
                          ? "border-accent/40 bg-accent-soft/60 text-accent-deep"
                          : "border-line bg-surface text-ink-soft hover:border-line-strong"
                      }`}
                    >
                      {on ? <CheckCircle size={15} weight="fill" /> : <X size={15} className="opacity-0" />}
                      {c.name}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {grantErr && <p className="text-xs text-danger">{grantErr}</p>}
        </div>
      </Modal>

      {/* 学科管理员授权（学科×年级，覆盖式） */}
      <Modal
        open={scopeFor !== null}
        onClose={() => setScopeFor(null)}
        title={scopeFor ? `学科授权 · ${scopeFor.name}` : "学科授权"}
        footer={
          <>
            <Button variant="ghost" onClick={() => setScopeFor(null)} disabled={scopeBusy}>
              取消
            </Button>
            <Button variant="primary" onClick={doSaveScopes} disabled={scopeBusy}>
              {scopeBusy ? "保存中…" : "保存授权"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-xs text-ink-faint">
            学科管理员在其（学科×年级）范围内可编辑知识库内容并启用版本（全校口径切换）。
            保存即覆盖该教师的全部学科授权；清空列表 = 撤销学科管理员。
          </p>
          {scopeDraft.length > 0 && (
            <ul className="flex flex-wrap gap-2">
              {scopeDraft.map((s) => (
                <li
                  key={`${s.subject}-${s.grade}`}
                  className="flex items-center gap-1.5 rounded-full bg-accent-soft px-3 py-1 text-xs font-medium text-accent-deep"
                >
                  {s.subject} · {s.grade} 年级
                  <button
                    onClick={() => setScopeDraft((d) => d.filter((x) => !(x.subject === s.subject && x.grade === s.grade)))}
                    aria-label={`移除 ${s.subject} ${s.grade} 年级`}
                    className="text-accent-deep/60 transition-colors hover:text-danger"
                  >
                    <X size={12} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap items-end gap-2">
            <Field label="学科">
              <Input
                value={scopeSubject}
                onChange={(e) => setScopeSubject(e.target.value)}
                list="scope-subjects"
                placeholder="如 数学"
                className="w-28"
              />
              <datalist id="scope-subjects">
                {COMMON_SUBJECTS.map((s) => (
                  <option key={s} value={s} />
                ))}
              </datalist>
            </Field>
            <Field label="年级">
              <Input
                type="number"
                value={scopeGrade}
                onChange={(e) => setScopeGrade(Number(e.target.value))}
                className="w-20"
              />
            </Field>
            <Button
              variant="secondary"
              disabled={!scopeSubject.trim()}
              onClick={() => {
                const key = scopeSubject.trim();
                if (scopeDraft.some((x) => x.subject === key && x.grade === scopeGrade)) return;
                setScopeDraft((d) => [...d, { subject: key, grade: scopeGrade }]);
                setScopeSubject("");
              }}
            >
              <Plus size={14} /> 添加
            </Button>
          </div>
          {scopeErr && <p className="text-xs text-danger">{scopeErr}</p>}
        </div>
      </Modal>
    </Page>
  );
}
