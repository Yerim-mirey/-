"""Shared wording for quota refusals, so three providers stay consistent.

The quota guard is a cross-provider concern, but the Tool contracts are not:
each provider still raises its own error class so the existing services keep
mapping failures to `RATE_LIMITED` / retryable envelopes. Only the message is
shared here.
"""

from __future__ import annotations


QUOTA_MESSAGE = "百度地图服务共享配额等待超时，请稍后重试。"
