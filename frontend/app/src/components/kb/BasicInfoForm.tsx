import { COMMON_EDITIONS, COMMON_SUBJECTS, derivePrefix } from "../../lib/kb-create";
import type { KbBasic } from "../../lib/kb-create";
import { Field, Input } from "../ui";

/** 建库步骤 1 基本信息表单：分步向导与思维导图两模式共用（kb-mindmap-create §4）。 */
export default function BasicInfoForm({
  value,
  onChange,
  showPrefix = false,
}: {
  value: KbBasic;
  onChange: (b: KbBasic) => void;
  showPrefix?: boolean;
}) {
  const setField = (patch: Partial<KbBasic>) => {
    const next = { ...value, ...patch };
    // 前缀仍是默认推导值 → 跟随学科/年级重推；手改过则不动
    if (next.codePrefix === derivePrefix(value.subject, value.grade)) {
      next.codePrefix = derivePrefix(next.subject, next.grade);
    }
    onChange(next);
  };
  return (
    <div className="space-y-4">
      <Field label="学科" hint="同一学科内做版本治理；分析按班级学科取对应知识库">
        <Input
          value={value.subject}
          onChange={(e) => setField({ subject: e.target.value })}
          list="kb-common-subjects"
          placeholder="如 数学"
        />
        <datalist id="kb-common-subjects">
          {COMMON_SUBJECTS.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
      </Field>
      <Field label="教材版本">
        <Input
          value={value.edition}
          onChange={(e) => setField({ edition: e.target.value })}
          list="kb-common-editions"
        />
        <datalist id="kb-common-editions">
          {COMMON_EDITIONS.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="版本号" hint="草稿，之后可启用">
          <Input value={value.version} onChange={(e) => setField({ version: e.target.value })} />
        </Field>
        <Field label="年级（数字，如 7 = 初一）">
          <Input type="number" value={value.grade} onChange={(e) => setField({ grade: Number(e.target.value) })} />
        </Field>
      </div>
      {showPrefix && (
        <Field label="编码前缀" hint="思维导图模式自动编码用（如 M7A-105 的前半段）；可改">
          <Input
            value={value.codePrefix ?? ""}
            onChange={(e) => setField({ codePrefix: e.target.value })}
            className="font-mono"
          />
        </Field>
      )}
    </div>
  );
}
