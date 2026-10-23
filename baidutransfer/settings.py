# -*- coding: utf-8 -*-

from dataclasses import dataclass


@dataclass(frozen=True)
class TransferSettings:
    """Runtime policy shared by the command line and desktop UI."""

    max_files_per_request: int = 500
    request_interval_min: int = 30
    request_interval_max: int = 60
    rest_after_min_requests: int = 10
    rest_after_max_requests: int = 20
    rest_duration_min: int = 60
    rest_duration_max: int = 300
    max_retries: int = 10
    http_total_timeout: int = 60
    http_connect_timeout: int = 15
    http_read_timeout: int = 45

    def validate(self):
        ranges = (
            ("max_files_per_request", self.max_files_per_request, 1, 500),
            ("request_interval_min", self.request_interval_min, 0, 3600),
            ("request_interval_max", self.request_interval_max, 0, 3600),
            ("rest_after_min_requests", self.rest_after_min_requests, 1, 1000),
            ("rest_after_max_requests", self.rest_after_max_requests, 1, 1000),
            ("rest_duration_min", self.rest_duration_min, 0, 86400),
            ("rest_duration_max", self.rest_duration_max, 0, 86400),
            ("max_retries", self.max_retries, 0, 100),
            ("http_total_timeout", self.http_total_timeout, 1, 3600),
            ("http_connect_timeout", self.http_connect_timeout, 1, 3600),
            ("http_read_timeout", self.http_read_timeout, 1, 3600),
        )
        for name, value, minimum, maximum in ranges:
            if not minimum <= value <= maximum:
                raise ValueError(
                    "%s must be between %d and %d" % (name, minimum, maximum)
                )
        pairs = (
            ("request interval", self.request_interval_min, self.request_interval_max),
            (
                "request-count rest trigger",
                self.rest_after_min_requests,
                self.rest_after_max_requests,
            ),
            ("rest duration", self.rest_duration_min, self.rest_duration_max),
        )
        for name, minimum, maximum in pairs:
            if minimum > maximum:
                raise ValueError("%s minimum cannot exceed maximum" % name)
        if self.http_connect_timeout > self.http_total_timeout:
            raise ValueError("connect timeout cannot exceed total timeout")
        if self.http_read_timeout > self.http_total_timeout:
            raise ValueError("read timeout cannot exceed total timeout")
        return self

