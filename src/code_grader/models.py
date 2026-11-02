from dataclasses import asdict, dataclass, field


class CollectionError(ValueError):
    """An input cannot be processed reliably."""


@dataclass
class Student:
    student_id: str
    name: str
    repository_urls: list[str] = field(default_factory=list)
    submission_urls: list[str] = field(default_factory=list)
    submission_status: str = ""


@dataclass
class SubmissionException:
    sheet: str
    row: int
    student_id: str
    name: str
    reason: str
    value: str


@dataclass
class Warning:
    sheet: str
    row: int | None
    student_id: str
    message: str
    code: str = ""


@dataclass
class Collection:
    students: list[Student] = field(default_factory=list)
    exceptions: list[SubmissionException] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    skipped_unsubmitted: int = 0

    def to_dict(self) -> dict:
        return asdict(self)
