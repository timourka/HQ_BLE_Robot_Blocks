/* global Blockly */
"use strict";

const C = {
  move: "#43A047", turn: "#1976D2", stop: "#E53935", flow: "#F59E0B",
  parallel: "#6941C6", vars: "#00897B", arrays: "#087E8B",
  controls: "#7E57C2", display: "#AB47BC", files: "#455A64", extra: "#607D8B"
};

function statementBlock(type, colour, build) {
  Blockly.Blocks[type] = {
    init() {
      build.call(this);
      this.setPreviousStatement(true);
      this.setNextStatement(true);
      this.setColour(colour);
      this.setInputsInline(false);
    }
  };
}

function exprField(block, label, name, value) {
  block.appendDummyInput().appendField(label).appendField(new Blockly.FieldTextInput(value), name);
}

["forward", "backward", "left", "right"].forEach((name) => {
  const labels = {forward: "вперёд", backward: "назад", left: "налево", right: "направо"};
  statementBlock(`hq_${name}`, name === "left" || name === "right" ? C.turn : C.move, function () {
    this.appendDummyInput()
      .appendField(labels[name])
      .appendField(new Blockly.FieldTextInput("1.0"), "DURATION")
      .appendField("сек.");
    this.setTooltip("Продолжительность можно задать выражением.");
  });
});

statementBlock("hq_stop", C.stop, function () {
  this.appendDummyInput().appendField("стоп");
});
statementBlock("hq_wait", C.flow, function () {
  this.appendDummyInput()
    .appendField("пауза")
    .appendField(new Blockly.FieldTextInput("1.0"), "DURATION")
    .appendField("сек.");
});

Blockly.Blocks.hq_repeat = {
  init() {
    this.appendDummyInput()
      .appendField("повторить")
      .appendField(new Blockly.FieldTextInput("2"), "COUNT")
      .appendField("раз");
    this.appendStatementInput("DO").appendField("выполнить");
    this.setPreviousStatement(true);
    this.setNextStatement(true);
    this.setColour(C.flow);
  }
};

Blockly.Blocks.hq_parallel = {
  init() {
    this.appendDummyInput().appendField("параллельно");
    this.appendStatementInput("BRANCH1").appendField("ветка 1");
    this.appendStatementInput("BRANCH2").appendField("ветка 2");
    this.appendStatementInput("BRANCH3").appendField("ветка 3 (необязательно)");
    this.setPreviousStatement(true);
    this.setNextStatement(true);
    this.setColour(C.parallel);
    this.setTooltip("Ветки выполняются одновременно.");
  }
};

statementBlock("hq_var_set", C.vars, function () {
  this.appendDummyInput()
    .appendField("задать")
    .appendField(new Blockly.FieldTextInput("x"), "NAME")
    .appendField("=")
    .appendField(new Blockly.FieldTextInput("0"), "EXPR");
});
statementBlock("hq_var_change", C.vars, function () {
  this.appendDummyInput()
    .appendField("изменить")
    .appendField(new Blockly.FieldTextInput("x"), "NAME")
    .appendField("на")
    .appendField(new Blockly.FieldTextInput("1"), "EXPR");
});
statementBlock("hq_var_math", C.vars, function () {
  this.appendDummyInput()
    .appendField("переменная")
    .appendField(new Blockly.FieldTextInput("x"), "NAME")
    .appendField(new Blockly.FieldDropdown([
      ["+", "+"], ["−", "-"], ["×", "*"], ["÷", "/"],
      ["//", "//"], ["%", "%"], ["**", "**"]
    ]), "OP")
    .appendField(new Blockly.FieldTextInput("2"), "EXPR");
});
statementBlock("hq_array_set", C.arrays, function () {
  this.appendDummyInput()
    .appendField("массив")
    .appendField(new Blockly.FieldTextInput("arr"), "NAME")
    .appendField("индекс")
    .appendField(new Blockly.FieldTextInput("0"), "INDEX");
  exprField(this, "значение", "VALUE", "0");
});
statementBlock("hq_array_append", C.arrays, function () {
  this.appendDummyInput()
    .appendField("добавить в")
    .appendField(new Blockly.FieldTextInput("arr"), "NAME")
    .appendField(new Blockly.FieldTextInput("0"), "VALUE");
});
statementBlock("hq_array_insert", C.arrays, function () {
  this.appendDummyInput()
    .appendField("вставить в")
    .appendField(new Blockly.FieldTextInput("arr"), "NAME")
    .appendField("по индексу")
    .appendField(new Blockly.FieldTextInput("0"), "INDEX");
  exprField(this, "значение", "VALUE", "0");
});
statementBlock("hq_array_pop", C.arrays, function () {
  this.appendDummyInput()
    .appendField("удалить из")
    .appendField(new Blockly.FieldTextInput("arr"), "NAME")
    .appendField("индекс")
    .appendField(new Blockly.FieldTextInput("-1"), "INDEX");
  exprField(this, "результат в (необязательно)", "TARGET", "");
});
statementBlock("hq_log", C.extra, function () {
  exprField(this, "вывести в журнал", "EXPR", "x");
});

statementBlock("hq_control_define", C.controls, function () {
  this.appendDummyInput()
    .appendField("создать управление")
    .appendField(new Blockly.FieldTextInput("speed"), "NAME")
    .appendField(new Blockly.FieldDropdown([
      ["ползунок", "slider"], ["число", "number"], ["флажок", "checkbox"],
      ["текст", "text"], ["кнопка", "button"]
    ]), "KIND");
  exprField(this, "подпись", "LABEL", "Скорость");
  this.appendDummyInput()
    .appendField("min")
    .appendField(new Blockly.FieldTextInput("0"), "MIN")
    .appendField("max")
    .appendField(new Blockly.FieldTextInput("100"), "MAX")
    .appendField("шаг")
    .appendField(new Blockly.FieldTextInput("1"), "STEP");
  this.appendDummyInput()
    .appendField("начальное")
    .appendField(new Blockly.FieldTextInput("50"), "DEFAULT")
    .appendField("переменная")
    .appendField(new Blockly.FieldTextInput("speed"), "BIND");
});
statementBlock("hq_control_read", C.controls, function () {
  this.appendDummyInput()
    .appendField("считать управление")
    .appendField(new Blockly.FieldTextInput("speed"), "NAME")
    .appendField("в")
    .appendField(new Blockly.FieldTextInput("speed"), "TARGET");
});
statementBlock("hq_control_write", C.controls, function () {
  this.appendDummyInput()
    .appendField("задать управление")
    .appendField(new Blockly.FieldTextInput("speed"), "NAME")
    .appendField("=")
    .appendField(new Blockly.FieldTextInput("50"), "VALUE");
});

statementBlock("hq_matrix", C.display, function () {
  this.appendDummyInput()
    .appendField("матрица 5×5")
    .appendField(new Blockly.FieldDropdown([
      ["из выражения", "expr"], ["из строк", "manual"]
    ]), "SOURCE");
  exprField(this, "выражение", "EXPR", "screen");
  exprField(this, "строки 0..31 через запятую", "ROWS", "0,0,0,0,0");
});
statementBlock("hq_marquee", C.display, function () {
  exprField(this, "бегущая строка", "TEXT", "'ПРИВЕТ'");
  this.appendDummyInput()
    .appendField("кадр")
    .appendField(new Blockly.FieldTextInput("0.15"), "FRAME")
    .appendField("сек., длительность")
    .appendField(new Blockly.FieldTextInput("0"), "DURATION");
});
statementBlock("hq_clear_matrix", C.display, function () {
  this.appendDummyInput().appendField("очистить матрицу");
});
statementBlock("hq_melody", "#00ACC1", function () {
  exprField(this, "мелодия №", "NUMBER", "0");
});

statementBlock("hq_file_read", C.files, function () {
  this.appendDummyInput()
    .appendField("прочитать файл")
    .appendField(new Blockly.FieldTextInput("'data.json'"), "PATH")
    .appendField(new Blockly.FieldDropdown([
      ["JSON", "json"], ["текст", "text"], ["строки", "lines"]
    ]), "MODE");
  exprField(this, "результат в", "TARGET", "data");
});
statementBlock("hq_file_write", C.files, function () {
  exprField(this, "записать файл", "PATH", "'data.json'");
  exprField(this, "значение", "VALUE", "data");
  this.appendDummyInput()
    .appendField(new Blockly.FieldDropdown([["JSON", "json"], ["текст", "text"]]), "MODE")
    .appendField(new Blockly.FieldCheckbox("FALSE"), "APPEND")
    .appendField("добавить в конец");
});
statementBlock("hq_file_list", C.files, function () {
  exprField(this, "список файлов папки", "PATH", "'.'");
  exprField(this, "результат в", "TARGET", "files");
});
statementBlock("hq_raw", C.extra, function () {
  exprField(this, "HEX-команда", "HEX", "00");
});
