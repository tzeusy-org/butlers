import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { projectModel } from "./check-e2e-api-mocks.mjs";

const source = readFileSync(new URL("../../src/butlers/api/routers/permissions.py", import.meta.url), "utf8");
const route = "/{butler}/{perm}";
const model = "PermissionSetResponse";
const project = (text) => projectModel(text, route, model);
function changeField(replacement) {
  const start = source.indexOf(`class ${model}(BaseModel):`);
  assert.notEqual(start, -1);
  return source.slice(0, start) + source.slice(start).replace("    granted: bool", replacement);
}

test("projects the actual bound permission model, including datetime", () => {
  assert.equal(project(source), "butler: string; permission: string; granted: boolean; reason: string; updated_at: string;");
});

test("projects the actual bound spend ceiling model, including float", () => {
  const spend = readFileSync(new URL("../../src/butlers/api/routers/spend.py", import.meta.url), "utf8");
  assert.equal(projectModel(spend, "/ceiling", "SpendCeiling"), "monthly_usd: number; updated_at: string;");
});

test("new required primitive fields enter the checked response type", () => {
  assert.match(project(changeField("    granted: bool\n    new_field: str")), /new_field: string;/);
});

for (const [name, replacement] of [
  ["default", "    granted: bool = True"],
  ["union", "    granted: bool | None"],
  ["unknown type", "    granted: list[str]"],
  ["duplicate field", "    granted: bool\n    granted: bool"],
  ["class validator", "    granted: bool\n    def validate(self): pass"],
]) {
  test(`refuses unsupported ${name} instead of omitting its semantics`, () => {
    assert.throws(() => project(changeField(replacement)), /Unsupported/);
  });
}

test("refuses inheritance, decorated models and shadowed declarations", () => {
  for (const changed of [
    source.replace(`class ${model}(BaseModel):`, `class ${model}(OtherModel):`),
    source.replace(`class ${model}(BaseModel):`, `@custom\nclass ${model}(BaseModel):`),
    source.replace(`class ${model}(BaseModel):`, `@custom(\n    option=True,\n)\n# retained comment\n\nclass ${model}(BaseModel):`),
    source + `\nclass ${model}(BaseModel):\n    other: str\n`,
    source + `\n${model} = OtherModel\n`,
  ]) assert.throws(() => project(changed), /Unsupported/);
});

test("refuses a changed response_model or route binding", () => {
  assert.throws(() => project(source.replace(`response_model=ApiResponse[${model}]`, "response_model=OtherModel")), /binding/);
  assert.throws(() => project(source.replace(`@router.put("${route}"`, '@router.put("/other"')), /binding/);
});
