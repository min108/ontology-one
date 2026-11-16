"""REST v2 error vocabulary. Legacy response bodies remain unchanged."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue

ERROR_STATUS: dict[str, int] = {
    "UNAUTHENTICATED": 401,
    "PERMISSION_DENIED": 403,
    "TENANT_DISABLED": 403,
    "SCOPE_NOT_AUTHORIZED": 403,
    "SOURCE_UNAUTHORIZED": 403,
    "SHARING_LEVEL_INSUFFICIENT": 403,
    "NOT_SCENARIO_MEMBER": 403,
    "SEPARATION_OF_DUTIES": 403,
    "PAGE_ONLY_ACTION": 403,
    "DEMO_ONLY": 403,
    "NOT_FOUND": 404,
    "VALIDATION_FAILED": 422,
    "EXPRESSION_INVALID": 422,
    "EVIDENCE_NOT_FOUND": 422,
    "SPEC_INVALID": 422,
    "NUMERIC_CHECK_FAILED": 422,
    "VERSION_CONFLICT": 409,
    "ITEM_CHANGED": 409,
    "DATA_CHANGED": 409,
    "WORKSPACE_CHANGED": 409,
    "PRECONDITION_FAILED": 409,
    "GATE_NOT_PASSED": 409,
    "CONFIRMATION_REQUIRED": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "RUN_BUSY": 409,
    "ROUTE_LOCKED": 409,
    "ASK_LIMIT_REACHED": 409,
    "OWNER_CONFIRM_REQUIRED": 409,
    "STATEMENT_WITHDRAWN": 409,
    "PRACTICE_LIMIT_REACHED": 409,
    "BINDING_INVALID": 409,
    "MODEL_NOT_CONFIGURED": 409,
    "ACTION_EXPIRED": 410,
    "UPLOAD_REJECTED": 413,
    "IMPORT_LIMIT_EXCEEDED": 413,
    "PDF_NO_TEXT": 422,
    "RATE_LIMITED": 429,
    "QUOTA_EXCEEDED": 429,
    "MODEL_UNAVAILABLE": 503,
    "RUNTIME_UNAVAILABLE": 503,
    "SANDBOX_UNAVAILABLE": 503,
    "MAINTENANCE_LOCKED": 503,
    "QUERY_TIMEOUT": 504,
    "INTERNAL_ERROR": 500,
}
ERROR_MESSAGES: dict[str, str] = {
    "UNAUTHENTICATED": "尚未登录或登录已过期，请重新登录。",
    "PERMISSION_DENIED": "你没有执行这一步的权限，请联系本机构管理员。",
    "TENANT_DISABLED": "该机构已停用，暂时无法进入。",
    "SCOPE_NOT_AUTHORIZED": "你没有该机构的查看授权，请联系本机构管理员。",
    "SOURCE_UNAUTHORIZED": "查询这张台账需要数据授权，请联系管理员开通。",
    "SHARING_LEVEL_INSUFFICIENT": "该机构的这部分内容只共享了定义，无法返回统计或明细。",
    "NOT_SCENARIO_MEMBER": "这一步需要场景成员身份，请先联系场景负责人。",
    "SEPARATION_OF_DUTIES": "你是这项内容的提交人或最后编辑人，需要由其他审核人员审核。",
    "PAGE_ONLY_ACTION": "这一步需要在对应页面办理，请返回工作台进入相关页面。",
    "DEMO_ONLY": "模拟演示只在演示空间提供。",
    "NOT_FOUND": "没有找到这项内容，它可能已被归档或你无权查看。",
    "VALIDATION_FAILED": "输入内容不符合要求，请核对后重试。",
    "EXPRESSION_INVALID": "判定条件或计算式不符合要求，请核对所用属性和规则。",
    "EVIDENCE_NOT_FOUND": "没能在原文中找到这段引文，请重新选择出处。",
    "SPEC_INVALID": "成果要求尚不完整，请补充后重试。",
    "NUMERIC_CHECK_FAILED": "成果中的数字未通过核验，请核对数据后重试。",
    "VERSION_CONFLICT": "这项内容已被他人更新，请刷新后重新核对。",
    "ITEM_CHANGED": "这条建议已被修改，请刷新后重新核对。",
    "DATA_CHANGED": "清单数据刚有更新，相关数字需要重新计算后再生成。",
    "WORKSPACE_CHANGED": "你已切换到另一个机构，本次操作没有执行。",
    "PRECONDITION_FAILED": "这一步的前置条件尚未满足，请核对所需信息后重试。",
    "GATE_NOT_PASSED": "发布检查尚未通过，请查看检查结果并补充所需信息。",
    "CONFIRMATION_REQUIRED": "这一步需要你确认后才会执行。",
    "IDEMPOTENCY_CONFLICT": "相同的请求正在处理或已处理，请勿重复提交。",
    "RUN_BUSY": "上一个任务还在进行，可以等它完成或先停止它。",
    "ROUTE_LOCKED": "这条体验路线正在使用中，请选择其他路线或稍后重试。",
    "ASK_LIMIT_REACHED": "未回答的请教已达到上限，请等待答复或联系其他成员。",
    "OWNER_CONFIRM_REQUIRED": "这次修改涉及其他已上线场景在用的公共内容，需要机构本体负责人确认后才能通过。",
    "STATEMENT_WITHDRAWN": "这条规则依据的说法已被本人撤回，请重新请教或改用其他依据。",
    "PRACTICE_LIMIT_REACHED": "练习区已满，请联系管理员清理闲置的练习区。",
    "BINDING_INVALID": "这张台账的结构发生了变化，需要重新核对列的对应关系。",
    "MODEL_NOT_CONFIGURED": "智能整理尚未开通，管理员可在“模型与运行”中配置；现在可以先手工整理。",
    "ACTION_EXPIRED": "这项确认已过期，可以重新发起。",
    "UPLOAD_REJECTED": "文件大小或类型不符合要求，请核对后重新上传。",
    "IMPORT_LIMIT_EXCEEDED": "内容超过上限，请缩小范围后重试。",
    "PDF_NO_TEXT": "这份文件没有可提取的文字，请提供可复制文字的PDF或Word版本。",
    "RATE_LIMITED": "操作太频繁了，请稍后再试。",
    "QUOTA_EXCEEDED": "今天的智能任务用量已达上限，请联系管理员调整。",
    "MODEL_UNAVAILABLE": "智能服务暂时无法连接，请稍后重试。",
    "RUNTIME_UNAVAILABLE": "助手的运行环境未就绪，管理员可在“模型与运行”中查看诊断。",
    "SANDBOX_UNAVAILABLE": "当前运行方式不支持技能测试，请在容器部署环境中使用。",
    "MAINTENANCE_LOCKED": "系统正在维护，请稍后重试。",
    "QUERY_TIMEOUT": "查询用时过长，请缩小范围后重试。",
    "INTERNAL_ERROR": "这次操作未能完成，请稍后重试；若问题持续，请联系管理员。",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NextAction(StrictModel):
    label: str
    href: str


class ErrorDetail(StrictModel):
    code: str
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)
    next_actions: list[NextAction] = Field(default_factory=list)
    request_id: str


class ErrorResponse(StrictModel):
    error: ErrorDetail


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        details: dict[str, JsonValue] | None = None,
        next_actions: list[NextAction] | None = None,
        status_code: int | None = None,
    ) -> None:
        if code not in ERROR_STATUS:
            raise ValueError("Unknown error code")
        expected = ERROR_STATUS[code]
        if (
            status_code is not None
            and status_code != expected
            and not (code == "UPLOAD_REJECTED" and status_code == 415)
        ):
            raise ValueError("Invalid status for error code")
        self.code = code
        self.message = message or ERROR_MESSAGES[code]
        self.status_code = status_code or expected
        self.details = details or {}
        if next_actions is None:
            action = {
                "PERMISSION_DENIED": ("返回工作台", "/"),
                "MODEL_NOT_CONFIGURED": ("返回工作台", "/"),
                "VERSION_CONFLICT": ("返回工作台重新进入", "/"),
                "PRECONDITION_FAILED": ("返回工作台", "/"),
            }.get(code)
            next_actions = [NextAction(label=action[0], href=action[1])] if action else []
        self.next_actions = next_actions
        super().__init__(self.message)

    def response(self, request_id: str) -> ErrorResponse:
        return ErrorResponse(
            error=ErrorDetail(
                code=self.code,
                message=self.message,
                details=self.details,
                next_actions=self.next_actions,
                request_id=request_id,
            )
        )


def from_http(status: int, message: str = "") -> ApiError:
    code = {
        401: "UNAUTHENTICATED",
        403: "PERMISSION_DENIED",
        404: "NOT_FOUND",
        409: "VERSION_CONFLICT",
        410: "ACTION_EXPIRED",
        413: "UPLOAD_REJECTED",
        415: "UPLOAD_REJECTED",
        422: "VALIDATION_FAILED",
        429: "RATE_LIMITED",
        503: "RUNTIME_UNAVAILABLE",
        504: "QUERY_TIMEOUT",
    }.get(status, "INTERNAL_ERROR")
    return ApiError(code, message if status < 500 else "", status_code=415 if status == 415 else None)
