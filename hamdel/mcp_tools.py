"""Small stdio MCP server with bounded charity tools and no arbitrary execution."""
import ast
import csv
import io
import json
import operator
import sys
from pathlib import Path

ROOT=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path.cwd()

def schema(properties, required=()):
    return {"type":"object","properties":properties,"required":list(required),"additionalProperties":False}

STRING={"type":"string"}
TOOLS=[
 {"name":"search_archive","description":"Search archived text and cached media evidence; returns bounded sources with message IDs and coverage.",
  "inputSchema":schema({"query":STRING,"from_date":STRING,"to_date":STRING,"limit":{"type":"integer","minimum":1,"maximum":20},"message_id":{"type":"integer"}})},
 {"name":"financial_report","description":"Calculate exact totals from OWNER-APPROVED donations only, optionally converting SEK to toman at the explicitly supplied rate.",
  "inputSchema":schema({"from_date":STRING,"to_date":STRING,"rate_toman_per_sek":{"type":"number","exclusiveMinimum":0}},["from_date","to_date"])},
 {"name":"export_financial_report","description":"Create an exact CSV or XLSX with approved transaction details directly from the ledger, without copying all rows into model context.",
  "inputSchema":schema({"from_date":STRING,"to_date":STRING,"filename":STRING},["from_date","to_date","filename"])},
 {"name":"workspace_summary","description":"Read approved ledger/content or pending drafts. Pending drafts are not confirmed facts.",
  "inputSchema":schema({"section":{"type":"string","enum":["summary","approved_content","pending"]}},["section"])},
 {"name":"calculate","description":"Evaluate basic arithmetic, without code execution or external access.",
  "inputSchema":schema({"expression":STRING},["expression"])},
 {"name":"create_file","description":"Create a UTF-8 report/table/text/SVG/HTML artifact in outputs. Return its relative path in the final artifacts array.",
  "inputSchema":schema({"filename":STRING,"content":STRING},["filename","content"])},
 {"name":"create_spreadsheet","description":"Create an XLSX workbook from headers and rows in outputs. Cells are literal values, not executable formulas.",
  "inputSchema":schema({"filename":STRING,"headers":{"type":"array","items":STRING},"rows":{"type":"array","items":{"type":"array","items":{"type":["string","number","boolean","null"]}}}},["filename","headers","rows"])}
]
for tool in TOOLS:
    tool['annotations']={'readOnlyHint':tool['name'] in ('search_archive','financial_report','workspace_summary','calculate'),
                         'destructiveHint':False,'openWorldHint':False}

def safe_path(name,suffixes):
    if not isinstance(name,str) or Path(name).name!=name or len(name)>120:
        raise ValueError('Use a plain filename')
    base=(ROOT/'outputs').resolve();base.mkdir(exist_ok=True)
    if not base.is_relative_to(ROOT):raise ValueError('Invalid outputs directory')
    dest=(base/name).resolve()
    if not dest.is_relative_to(base) or dest.suffix.lower() not in suffixes:raise ValueError('Invalid output path')
    return dest

def arithmetic(expression):
    if not isinstance(expression,str) or len(expression)>150:raise ValueError('Expression too long')
    functions={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.FloorDiv:operator.floordiv,ast.Mod:operator.mod}
    def number(node):
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):result=node.value
        elif isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            result=number(node.operand)*(1 if isinstance(node.op,ast.UAdd) else -1)
        elif isinstance(node,ast.BinOp) and type(node.op) in functions:result=functions[type(node.op)](number(node.left),number(node.right))
        else:raise ValueError('Only basic arithmetic is supported')
        if abs(result)>10**15:raise ValueError('Number too large')
        return result
    return number(ast.parse(expression,mode='eval').body)

def call(name,args):
    definition=next((t for t in TOOLS if t['name']==name),None)
    if not definition:raise ValueError('Unknown tool')
    if not isinstance(args,dict) or set(args)-set(definition['inputSchema']['properties']):raise ValueError('Invalid arguments')
    for required in definition['inputSchema']['required']:
        if required not in args:raise ValueError('Missing '+required)
    if name=='search_archive':
        from archive import search
        return search(ROOT/'context/archive_index.json',args.get('query',''),args.get('from_date'),args.get('to_date'),args.get('limit',6),args.get('message_id'))
    if name=='financial_report':
        from report import calculate
        return calculate(ROOT/'context/approved_donations.csv',args['from_date'],args['to_date'],args.get('rate_toman_per_sek'))
    if name=='export_financial_report':
        from report import calculate
        summary=calculate(ROOT/'context/approved_donations.csv',args['from_date'],args['to_date'])
        with (ROOT/'context/approved_donations.csv').open(encoding='utf-8',newline='') as f:
            reader=csv.DictReader(f);headers=reader.fieldnames
            rows=[[row[k] for k in headers] for row in reader if args['from_date']<=row['date']<=args['to_date']]
        if Path(args['filename']).suffix.lower()=='.xlsx':
            result=call('create_spreadsheet',{'filename':args['filename'],'headers':headers,'rows':rows})
        else:
            out=io.StringIO();writer=csv.writer(out);writer.writerow(headers);writer.writerows(rows)
            result=call('create_file',{'filename':args['filename'],'content':out.getvalue()})
        return result | {'summary':summary}
    if name=='workspace_summary':
        section=args['section']
        if section not in ('summary','approved_content','pending'):raise ValueError('Invalid section')
        value=json.loads((ROOT/'context'/f'{section}.json').read_text())
        return value[:20] if isinstance(value,list) else value
    if name=='calculate':return {'result':arithmetic(args['expression'])}
    if name=='create_file':
        path=safe_path(args['filename'],{'.csv','.txt','.md','.json','.html','.svg'})
        content=args['content']
        if not isinstance(content,str) or len(content)>300000:raise ValueError('File too large')
        if path.suffix=='.csv':
            # Neutralize spreadsheet formula injection in generated public exports.
            rows=list(csv.reader(io.StringIO(content)));out=io.StringIO();writer=csv.writer(out)
            for row in rows:writer.writerow(["'"+v if v.startswith(('=','+','-','@','\t','\r')) else v for v in row])
            content=out.getvalue()
        path.write_text(content,encoding='utf-8-sig' if path.suffix=='.csv' else 'utf-8')
        return {'path':str(path.relative_to(ROOT)),'bytes':path.stat().st_size}
    if name=='create_spreadsheet':
        from openpyxl import Workbook
        from openpyxl.styles import Font,PatternFill
        path=safe_path(args['filename'],{'.xlsx'});headers=args['headers'];rows=args['rows']
        if len(headers)>40 or len(rows)>10000 or any(len(row)>40 for row in rows):raise ValueError('Table too large')
        wb=Workbook();ws=wb.active;ws.title='همدل';ws.sheet_view.rightToLeft=True
        ws.append(headers)
        for row in rows:
            if any(type(v) not in (str,int,float,bool,type(None)) for v in row):raise ValueError('Invalid cell')
            ws.append(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for v in row])
        for cell in ws[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='276749')
        ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
        wb.save(path);return {'path':str(path.relative_to(ROOT)),'rows':len(rows)}

def respond(message):
    method=message.get('method');ident=message.get('id')
    if ident is None:return None
    if method=='initialize':result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'hamdel','version':'3.0'}}
    elif method=='ping':result={}
    elif method=='tools/list':result={'tools':TOOLS}
    elif method=='tools/call':
        try:
            params=message.get('params',{});obj=call(params['name'],params.get('arguments',{}))
            result={'content':[{'type':'text','text':json.dumps(obj,ensure_ascii=False)}]}
        except Exception as exc:
            result={'content':[{'type':'text','text':'Tool failed: '+type(exc).__name__+': '+str(exc)[:160]}],'isError':True}
    else:return {'jsonrpc':'2.0','id':ident,'error':{'code':-32601,'message':'Unknown method'}}
    return {'jsonrpc':'2.0','id':ident,'result':result}

if __name__=='__main__':
    for line in sys.stdin:
        try:
            result=respond(json.loads(line))
            if result is not None:print(json.dumps(result,ensure_ascii=False),flush=True)
        except (ValueError,TypeError):pass
