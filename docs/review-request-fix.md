# Review request flow, 28 September 2026

The dedicated review route now uses saved customer details, renders the selected channel before sending, and requires a signed preview snapshot plus explicit POST. Single-use attempt IDs prevent replay. Owner tests have a separate recipient preview and are not associated with the customer, their timeline or review state. Provider acceptance is labelled delivery unconfirmed; failed and interrupted attempts are separate outcomes. No live messages were sent for validation.

Manual authenticated POST SMS is enabled. AUTOMATED_CUSTOMER_MESSAGES_PAUSED remains true, as does CUSTOMER_OUTBOUND_APPROVAL_REQUIRED. The acknowledgement worker, communication worker and direct automation-rule helper cannot send. Five-minute acknowledgement creation remains Awaiting approval without a timer; scheduled/no-reply follow-ups create review tasks only. Low-level customer SMS outside authenticated POST is also blocked. Owner notifications retain their separate paths. SMS opt-outs and UK-mobile validation remain enforced.

The previous owner test used the customer object despite substituting the owner's recipient, so the blanket SMS pause rejected it before a provider call. The old route nevertheless logged a customer communication and redirected with sent=1. The live SMS log contained no send for that date. The narrowly identified Ruby record (communication384, customer747, 28Sep2026, matching name/subject, no SMS event) is retained with its original body as a blocked-test audit entry and excluded from sent-state inference. Other history is untouched.

Validation: focused tests cover preview/no-send, authoritative recipients, stale and altered snapshots, duplicate submission, owner-test isolation, failure/demo/pending responses, actual mocked provider path, manual versus background controls, legacy queues and narrow history repair. Email and SMS layouts checked at desktop and 390px phone width. Existing security-suite failures reproduced on the unchanged baseline: cache-version assertions, job-summary and mobile-sidebar assertions, inbound-notification request-context fixture.

## Follow-up UI correction

The recipient switch now sits immediately below Email/Text, with customer first and the separate test target secondary. Changing channels returns to the customer target. A fixed final action stays visible above mobile navigation. Last attempt outcomes persist when reopening the same channel/target; email-provider errors receive actionable explanations without raw credentials.

Live read-only diagnosis after the regression report found no review_send_attempts for Ruby and only GET requests for the new owner-test email and SMS previews. There was no email POST or email-provider error in that period. The only delivery failure was the earlier blocked SMS. No email configuration was changed without evidence, and no live diagnostic messages were sent.
