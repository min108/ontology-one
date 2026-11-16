// Use the fresh browser context's real cookie jar before its first application
// navigation. The unauthenticated login page and UI login behavior have separate
// acceptance tests; authenticated visuals must reject every page/console error.
export async function openAuthenticatedVisualPage(context, page, account) {
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(message.text());
  });
  const response = await context.request.post("/api/auth/login", {
    data: { username: account.username, password: account.password },
    headers: { "User-Agent": await page.evaluate(() => navigator.userAgent) },
  });
  try {
    if (response.status() !== 200)
      throw new Error(
        `Visual fixture login failed (HTTP ${response.status()})`,
      );
  } finally {
    // Never inspect, persist or include the session payload in failure output.
    await response.dispose();
  }
  await page.goto("/");
  return problems;
}
