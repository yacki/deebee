import assert from 'node:assert/strict';
import test from 'node:test';
import { cellText, formatCellJson, enumOptions, isGenerated, parseCellValue, parsePastedValue, parseDelimited, delimitedText } from '../src/cellValues.ts';
const col=(data_type,nullable=true)=>({name:'v',data_type,nullable});
for (const [type,text,expected] of [
 ['bigint unsigned','18446744073709551615','18446744073709551615'],
 ['bigint','-9223372036854775808','-9223372036854775808'],
 ['decimal(30,10)','12345678901234567890.1234567890','12345678901234567890.1234567890'],
 ['tinyint(1)','true',1],['bit(8)','0x80',128],['json','null','null'],['json','{"a":[1]}','{"a":[1]}'],
 ['varchar(20)','',''],['time(6)','-25:12:34.123456','-25:12:34.123456'],
 ['varbinary(20)','0x00ff',{$binary:'00ff',size:2}],
])test(`typed editor preserves ${type}`,()=>assert.deepEqual(parseCellValue(text,col(type)),expected));
test('NULL is explicit and independent of empty strings and JSON null',()=>{
 assert.equal(parseCellValue('',col('varchar(20)')), '');assert.equal(parseCellValue('',col('varchar(20)'),true),null);
 assert.equal(parseCellValue('null',col('json')),'null');assert.throws(()=>parseCellValue('',col('int',false),true));
});
test('invalid values are rejected before sending a save',()=>{
 for(const [type,value] of [['json','{bad'],['int','1.2'],['decimal(10,2)','NaN'],['blob','0x0']])assert.throws(()=>parseCellValue(value,col(type)));
});
test('copied multiline rectangular regions paste back without column or row corruption',()=>{
 const data=[['中文 😀','quote " tab\t','line1\nline2',''],['\\N','a','b','c']];
 assert.deepEqual(parseDelimited(delimitedText(data)).map(row=>row.map(text=>parsePastedValue(text,col('text')))),data);
});
test('enum options preserve original case and SQL quoting',()=>assert.deepEqual(enumOptions(col("enum('Alpha','it''s','Beta')")),['Alpha',"it's",'Beta']));
test('DEFAULT_GENERATED timestamps remain editable while computed and identity columns are read only',()=>{
 assert.equal(isGenerated({name:'created',extra:'DEFAULT_GENERATED'}),false);
 for(const extra of ['VIRTUAL GENERATED','STORED GENERATED','auto_increment','IDENTITY'])assert.equal(isGenerated({name:'computed',extra}),true);
});
test('BIT draft values use integer form while binary data keeps hexadecimal',()=>{
 assert.equal(cellText({$binary:'ff',size:1},col('bit(8)')),'255');assert.equal(cellText({$binary:'00ff',size:2},col('blob')),'0x00ff');
});

test('clipboard NULL markers remain distinct from literal marker text',()=>{
 const row=[null,'\\N','','\\\\server'];
 assert.deepEqual(parseDelimited(delimitedText([row]))[0].map(text=>parsePastedValue(text,col('text'))),row);
});

test('formatting JSON preserves integer and decimal lexemes',()=>{
 const raw='{"n":18446744073709551615,"d":0.123456789012345678901,"s":"quoted \\" text","a":[1,null,{}]}';
 const pretty=formatCellJson(raw);assert.ok(pretty.includes('18446744073709551615'));assert.ok(pretty.includes('0.123456789012345678901'));assert.deepEqual(JSON.parse(pretty),JSON.parse(raw));
});

test('a single empty string can be copied and pasted without turning into no data',()=>assert.deepEqual(parseDelimited(delimitedText([['']])),[['']]));
