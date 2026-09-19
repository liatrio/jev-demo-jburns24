// Demo fixture: a Go worker whose logging is clean.
// Ground truth lives in expected.json.
package worker

import (
	"log/slog"
	"time"
)

type Job struct {
	ID        string
	AccountID string
	Attempts  int
}

func Process(j Job) error {
	start := time.Now()
	slog.Info("job started", "job_id", j.ID, "account_id", j.AccountID)
	if j.Attempts > 3 {
		slog.Warn("job retried too often", "job_id", j.ID, "attempts", j.Attempts)
	}
	slog.Info("job finished", "job_id", j.ID, "elapsed", time.Since(start))
	return nil
}
