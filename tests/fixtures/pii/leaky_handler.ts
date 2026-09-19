// Demo fixture: an Express-style handler. Mixed clean and leaking log lines.
// Ground truth lives in expected.json.

import pino from "pino";

const log = pino();

interface User {
  id: string;
  name: string;
  email: string;
  dateOfBirth: string;
  sessionToken: string;
}

export function login(user: User, requestId: string): void {
  log.info({ requestId }, "login attempt");
  log.info(`user ${user.name} (${user.email}) logged in, dob ${user.dateOfBirth}`);
  console.error("session issued", { userId: user.id, token: user.sessionToken });
  log.warn({ requestId, userId: user.id, durationMs: 180 }, "slow login");
}

export function logout(userId: string): void {
  console.log("logout", { userId });
}
