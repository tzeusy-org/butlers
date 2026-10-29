import type { paths } from "./schema.gen";

/** Browser paths are below the one API mount, just like resolveApiHref. */
type ApiPaths = {
  [P in keyof paths as P extends `/api${infer Route}` ? Route : never]: paths[P];
};
export type ApiPath = keyof ApiPaths;
type Verb = "get" | "post" | "put" | "patch" | "delete";
export type ApiMethod<P extends ApiPath> = Uppercase<{
  [M in Verb]: ApiPaths[P][M] extends undefined ? never : M;
}[Verb]>;
type Operation<P extends ApiPath, M extends ApiMethod<P>> = NonNullable<
  ApiPaths[P][Lowercase<M> & keyof ApiPaths[P]]
>;
type ParametersOf<O, K extends string> = O extends { parameters: infer Params }
  ? K extends keyof Params ? NonNullable<Params[K]> : never
  : never;
export type ApiQuery<P extends ApiPath, M extends ApiMethod<P> = Extract<"GET", ApiMethod<P>>> =
  ParametersOf<Operation<P, M>, "query">;
type PathValues<P extends ApiPath, M extends ApiMethod<P>> = ParametersOf<Operation<P, M>, "path">;
type JsonContent<T> = T extends { content: { "application/json": infer Value } } ? Value : never;
type Responses<P extends ApiPath, M extends ApiMethod<P>> = Operation<P, M> extends {
  responses: infer R;
} ? R : never;
type Successful<R> = R[Extract<keyof R, 200 | 201 | 202 | 203 | 206 | 207>];
export type ApiResult<P extends ApiPath, M extends ApiMethod<P>> =
  JsonContent<Successful<Responses<P, M>>> |
  (204 extends keyof Responses<P, M> ? void : never);
type RequestBody<P extends ApiPath, M extends ApiMethod<P>> = Operation<P, M> extends {
  requestBody?: infer Body;
} ? JsonContent<NonNullable<Body>> : never;
type Field<K extends string, V> = [V] extends [never] ? { [Name in K]?: never }
  : Record<never, never> extends V ? { [Name in K]?: V } : { [Name in K]: V };
export type ApiOptions<P extends ApiPath, M extends ApiMethod<P>> = P extends ApiPath ?
  Omit<RequestInit, "method" | "body"> & { timeoutMs?: number; omitEmptyQuery?: readonly (keyof ApiQuery<P, M>)[] } &
  (M extends "GET" ? { method?: M } : { method: M }) &
  Field<"path", PathValues<P, M>> & Field<"query", ApiQuery<P, M>> &
  (Operation<P, M> extends { requestBody: unknown }
    ? { body: RequestBody<P, M> } : { body?: RequestBody<P, M> }) : never;
export type ApiArguments<P extends ApiPath, M extends ApiMethod<P>> =
  Record<never, never> extends ApiOptions<P, M> ? [options?: ApiOptions<P, M>] : [options: ApiOptions<P, M>];
