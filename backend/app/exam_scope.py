"""考试参与范围：全班考试与个体诊断共用同一个采集、预检和报告约束。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DiagnosticTask, ExamTemplate, Student


def diagnostic_task(session: Session, exam: ExamTemplate) -> DiagnosticTask | None:
    return session.scalar(select(DiagnosticTask).where(DiagnosticTask.exam_id == exam.id))


def exam_students(session: Session, exam: ExamTemplate) -> list[Student]:
    task = diagnostic_task(session, exam)
    stmt = select(Student).where(Student.class_id == exam.class_id)
    if task is not None:
        stmt = stmt.where(Student.id == task.student_id)
    elif exam.source == "diagnostic_task":
        # 历史记录没有保存目标，不能通过姓名或现有作答猜测授权对象。
        return []
    return list(session.scalars(stmt.order_by(Student.id)))


def assert_exam_student(session: Session, exam: ExamTemplate, student_id: int) -> None:
    student = session.get(Student, student_id)
    if student is None or student.class_id != exam.class_id:
        raise ValueError("学生不属于该班级")
    task = diagnostic_task(session, exam)
    if task is not None and task.student_id != student_id:
        raise ValueError("该诊断任务仅面向创建时指定的学生")
    if task is None and exam.source == "diagnostic_task":
        raise ValueError("该历史诊断任务缺少目标信息，请重新预览建卷")
