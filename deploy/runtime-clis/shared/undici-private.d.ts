/** Version-pinned internal entry points avoid the package root's global dispatcher initialization. */
declare module "butlers-egress-undici/lib/dispatcher/agent.js" {
  import type { Agent as PublicAgent, Dispatcher } from "butlers-egress-undici";
  export default class Agent {
    constructor(options?: PublicAgent.Options);
    dispatch: Dispatcher["dispatch"];
    destroy: Dispatcher["destroy"];
  }
}

declare module "butlers-egress-undici/lib/api/api-request.js" {
  import type { Dispatcher } from "butlers-egress-undici";
  export default function request(
    this: Pick<Dispatcher, "dispatch">,
    options: Dispatcher.RequestOptions,
  ): Promise<Dispatcher.ResponseData>;
}
