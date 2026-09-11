import { BookOpen, ChalkboardTeacher, CheckCircle, Plus, UserGear, X } from "@phosphor-icons/react";
import { useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorState, Field, Input, Modal, Page, PageHeader, Skeleton } from "../../components/ui";
import {
  createTeacher,
  grantTeacherClasses,
  listClasses,
  listTeachers,
  setTeacherKbEditor,
  type TeacherAccountRow,
} from "../../lib/api";
import { useAsync } from "../../lib/hooks";
import { ACCENTS } from "../../lib/theme";

/**
 * 账号管理（frontend-ends-design §C 校务台）：admin-only。
 *
 * 「教师账号」：列全部教师（name/username/admin/kb_editor/已授班级），新建教师，
 * 行内授权班级 + 知识库编辑权（两层写权的内容层授权面）。
 * 学生自服务账号在「班级 → 学生」行内开通（见 Students 页 §D），本页只作引导说明。
 * 单校部署：school_id 取首个班级所属学校（/classes 已带）；无任何班级时提示先初始化。
 */

interface CreateForm {
  name: string;
  username: string;
  password: string;
  admin: boolean;
  kb_editor: boolean;
}

const EMPTY_FORM: CreateForm = { name: "", username: "", password: "", admin: false, kb_editor: false };

export default function Accounts() {
  const teachers = useAsync(() => listTeachers(), []);
  const classes = useAsync(() => listClasses(), []);
  const classRows = classes.data?.classes ?? [];

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

  const openGrant = (t: TeacherAccountRow) => {
    setGrantFor(t);
    setGrantSel(new Set(t.classes.map((c) => c.class_id)));
    setGrantErr(null);
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

  const rows = teachers.data?.teachers ?? [];
  const toggle = (id: number) =>
    setGrantSel((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <Page accent={ACCENTS.dashboard}>
      <PageHeader
        title="账号管理"
        desc="教师账号、班级授权与知识库编辑权（学生自服务账号在对应班级的学生列表开通）"
        actions={
          schoolId ? (
            <Button onClick={() => { setCreateForm(EMPTY_FORM); setCreateErr(null); setShowCreate(true); }}>
              <Plus size={15} /> 新建教师
            </Button>
          ) : undefined
        }
      />

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
                <p className="flex items-center gap-2 truncate text-sm font-medium">
                  {t.name}
                  {t.admin && <Badge tone="accent">管理员</Badge>}
                  {t.kb_editor && <Badge tone="neutral">知识库编辑</Badge>}
                </p>
                <p className="flex items-center gap-2 text-xs text-ink-faint">
                  <span className="font-mono">{t.username ?? "（未设登录名）"}</span>
                  <span>
                    已授班级 {t.classes.length}：
                    {t.classes.length > 0
                      ? t.classes.map((c) => c.name).join("、")
                      : "未授权（admin 全校可见不受限）"}
                  </span>
                </p>
              </div>
              {!t.admin && (
                <Button
                  variant={t.kb_editor ? "primary" : "secondary"}
                  onClick={() => doKbEditor(t, !t.kb_editor)}
                  disabled={kbBusy === t.teacher_id}
                  title="知识库内容编辑权：录知识点/关系、fork 草稿版本；「设为正式版」仍需管理员"
                >
                  <BookOpen size={14} />
                  {kbBusy === t.teacher_id ? "切换中…" : t.kb_editor ? "知识库编辑 · 已授权" : "知识库编辑"}
                </Button>
              )}
              <Button variant="secondary" onClick={() => openGrant(t)}>
                <UserGear size={14} /> 授权班级
              </Button>
            </div>
          ))}
        </Card>
      )}

      {kbErr && <p className="mt-2 text-xs text-danger">{kbErr}</p>}

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
              className="accent-[#14b8a6]"
            />
            设为管理员（全校可见 + 校务台）
          </label>
          <label className="flex items-center gap-2 text-sm text-ink-soft">
            <input
              type="checkbox"
              checked={createForm.kb_editor}
              onChange={(e) => setCreateForm({ ...createForm, kb_editor: e.target.checked })}
              className="accent-[#14b8a6]"
            />
            授予知识库编辑权（录知识点/关系；切换正式版仍需管理员）
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
    </Page>
  );
}
