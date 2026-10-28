import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { projectModel } from "./check-e2e-api-mocks.mjs";

const source = readFileSync(new URL("../../src/butlers/api/routers/permissions.py", import.meta.url), "utf8");
const route = "/{butler}/{perm}";
const model = "PermissionSetResponse";
const project = (text) => projectModel(text, route, model);
function changeField(replacement) {
  const start = source.indexOf(`class ${model}(ResponseSchemaModel):`);
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
  for (const separator of ["", "# A column-zero comment does not end the class.\n"]) {
    assert.match(project(changeField(`    granted: bool\n${separator}    new_field: str`)), /new_field: string;/);
  }
});

for (const [name, replacement] of [
  ["default", "    granted: bool = True"],
  ["union", "    granted: bool | None"],
  ["unknown type", "    granted: list[str]"],
  ["duplicate field", "    granted: bool\n    granted: bool"],
  ["class validator", "    granted: bool\n    def validate(self): pass"],
]) {
  test(`refuses unsupported ${name} instead of omitting its semantics`, () => {
    for (const separator of ["", "# A column-zero comment does not end the class.\n"]) {
      assert.throws(() => project(changeField(separator + replacement)), /Unsupported/);
    }
  });
}

test("refuses inheritance, decorated models and shadowed declarations", () => {
  const base = readFileSync(new URL("../../src/butlers/api/models/base.py", import.meta.url), "utf8");
  assert.equal(projectModel(source, route, model, base), project(source));
  for (const poisonedBase of [
    base.replace("json_schema_serialization_defaults_required=True", "json_schema_serialization_defaults_required=False"),
    base.replace("json_schema_serialization_defaults_required=True", "extra=\"allow\""),
    base + "\nResponseSchemaModel = BaseModel\n",
    base + "\nclass Other(BaseModel): pass\n",
    base.replace("    model_config", "    extra_field: str\n    model_config"),
  ]) assert.throws(() => projectModel(source, route, model, poisonedBase), /Unsupported/);
  for (const changed of [
    source.replace(`class ${model}(ResponseSchemaModel):`, `class ${model}(OtherModel):`),
    source.replace(`class ${model}(ResponseSchemaModel):`, `@custom\nclass ${model}(ResponseSchemaModel):`),
    source.replace(`class ${model}(ResponseSchemaModel):`, `@custom(\n    option=True,\n)\n# retained comment\n\nclass ${model}(ResponseSchemaModel):`),
    source + `\nclass ${model}(ResponseSchemaModel):\n    other: str\n`,
    source + `\n${model} = OtherModel\n`,
  ]) assert.throws(() => project(changed), /Unsupported/);
  for (const [module, name, path] of [
    ["permissions", "PermissionSetResponse", "/{butler}/{perm}"],
    ["spend", "SpendCeiling", "/ceiling"],
  ]) {
    const actual = readFileSync(new URL(`../../src/butlers/api/routers/${module}.py`, import.meta.url), "utf8");
    const declaration = `class ${name}(ResponseSchemaModel):`;
    const start = actual.indexOf(declaration);
    const end = actual.indexOf("\n\n@router.put", start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);
    const literal = actual.slice(0, start) + "'''" + actual.slice(start, end) + "'''\n" +
      `from pydantic import BaseModel as ${name}\n` + actual.slice(end);
    const scoped = (header) => actual.slice(0, start) + header + "\n" +
      actual.slice(start, end).split("\n").map((line) => "    " + line).join("\n") + actual.slice(end);
    for (const changed of [
      literal,
      scoped("if False:"),
      scoped("def inactive():"),
      actual + `\n${name}: type[BaseModel] = BaseModel\n`,
      actual + `\nfrom pydantic import BaseModel as ${name}\n`,
      actual + `\nAlias = ${name}\n`,
      actual + `\nmatch {}:\n    case {**${name}}:\n        pass\n`,
      actual + "\nAlias = BaseModel\n",
      actual + "\nAlias = ResponseSchemaModel\n",
      actual + "\nResponseSchemaModel = BaseModel\n",
      actual + "\nfrom elsewhere import ResponseSchemaModel\n",
      actual + "\nAlias = ApiResponse\n",
      actual + "\nAlias = APIRouter\n",
      actual + "\nAlias = router\n",
      actual.replace(declaration, `@custom(\noption=True,\n)\n${declaration}`),
      actual + "\nif :\n",
    ]) assert.throws(() => projectModel(changed, path, name), /Unsupported/);
  }
});

test("refuses a changed response_model or route binding", () => {
  assert.throws(() => project(source.replace(`response_model=ApiResponse[${model}]`, "response_model=OtherModel")), /binding/);
  assert.throws(() => project(source.replace(`@router.put("${route}"`, '@router.put("/other"')), /binding/);
  for (const [module, name, path, prefix] of [
    ["permissions", "PermissionSetResponse", "/{butler}/{perm}", "/api/permissions"],
    ["spend", "SpendCeiling", "/ceiling", "/api/spend"],
  ]) {
    const actual = readFileSync(new URL(`../../src/butlers/api/routers/${module}.py`, import.meta.url), "utf8");
    const binding = `@router.put("${path}", response_model=ApiResponse[${name}])`;
    const wrong = binding.replace(`ApiResponse[${name}]`, "ApiResponse[BaseModel]");
    assert.ok(actual.includes(binding));
    for (const inert of [`# ${binding}\n`, `'''${binding}'''\n`]) {
      assert.throws(() => projectModel(actual.replace(binding, inert + wrong), path, name), /binding/);
    }
    assert.throws(() => projectModel(actual.replace(`prefix="${prefix}"`, 'prefix="/api/other"'), path, name), /binding/);
    assert.throws(() => projectModel(actual.replace(binding, binding + "\n" + binding), path, name), /binding/);
    const start = actual.indexOf(binding);
    for (const header of ["if False:", "def inactive():"]) {
      const nested = actual.slice(0, start) + header + "\n" +
        actual.slice(start).split("\n").map((line) => "    " + line).join("\n");
      assert.throws(() => projectModel(nested, path, name), /binding/);
    }
  }
});
