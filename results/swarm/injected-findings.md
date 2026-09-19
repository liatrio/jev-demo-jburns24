200 agents, 806 browser steps, 813 Jev calls, 38.3s wall, $0.0481. Injected: dead_link, idor, negative_transfer, overdraft, stack_trace, unicode_crash, xss_search. Result: FAIL.

| sev | category | path | agents | p(defect) | automatic checks | status | action |
|--:|---|---|--:|--:|---|---|---|
| 2 | money_loss | `/transfer` | 48 | 0.93 | negative_amount_displayed | **BLOCK** | click button "Send transfer" (submits the form) |
| 2 | broken_page | `/statements` | 25 | 0.92 | http_404 | **BLOCK** | click link "Statements" (goes to /statements) |
| 2 | security | `/accounts/2001` | 16 | 0.95 | - | **BLOCK** | click link "2001" (goes to /accounts/2001) |
| 2 | broken_page | `/transfer` | 12 | 0.90 | http_500, stack_trace_exposed | **BLOCK** | click button "Send transfer" (submits the form) |
| 2 | broken_page | `/profile` | 3 | 0.93 | http_500, stack_trace_exposed | **BLOCK** | click button "Save profile" (submits the form) |
| 2 | security | `/search` | 3 | 0.89 | script_injection_executed | **BLOCK** | click button "Search" (submits the form) |
| 2 | validation_gap | `/transfer` | 1 | 0.88 | - | report | click button "Send transfer" (submits the form) |
