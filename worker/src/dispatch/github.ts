const DISPATCH_URL =
  "https://api.github.com/repos/Labhary/btc-sentinel/actions/workflows/paper-engine.yml/dispatches";

export interface WorkflowDispatcher {
  dispatch(dispatchKey: string): Promise<void>;
}

export class GitHubWorkflowDispatcher implements WorkflowDispatcher {
  constructor(private readonly token: string) {}

  async dispatch(dispatchKey: string): Promise<void> {
    const response = await fetch(DISPATCH_URL, {
      method: "POST",
      redirect: "error",
      headers: {
        accept: "application/vnd.github+json",
        authorization: `Bearer ${this.token}`,
        "content-type": "application/json",
        "user-agent": "btc-sentinel-worker",
        "x-github-api-version": "2022-11-28",
      },
      body: JSON.stringify({ ref: "main", inputs: { dispatch_key: dispatchKey } }),
    });
    if (response.status !== 204) {
      throw new Error(`GITHUB_DISPATCH_${response.status}`);
    }
  }
}
