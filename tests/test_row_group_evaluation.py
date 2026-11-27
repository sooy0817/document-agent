import pytest

from scripts import evaluate_row_groups as ev


def test_paired_model_evaluation(tmp_path, monkeypatch):
    targets = ["C1","C2","C3","C4","C5"]
    packet = dict(cells=[{"id":s} for s in ["C0",*targets]],targets=targets)
    packets = {"T":packet}
    meta = dict(fingerprint=ev.token(packet),family="family",split="holdout")
    ev.write(tmp_path/"inputs.json",packets)
    ev.write(tmp_path/"baseline.json",{"T":dict(**meta,paths=dict(zip(targets,[None,[],[],[],None])))})
    gold = {"T":dict(**meta,reviewer="human",answers=[
        dict(target=t,state="present" if i == 0 else "none",parents=["C0"] if i == 0 else [])
        for i,t in enumerate(targets)])}
    ev.write(tmp_path/"reference.json",gold)

    def call(name,system,user,schema,policy,output,**kwargs):
        assert user == packet
        states = ["present","present","unknown","none","none"]
        return schema(answers=[ev.Answer(target=t,state=s,parents=["C0"] if s=="present" else [])
                               for t,s in zip(targets,states)]), {}

    monkeypatch.setattr(ev,"structured_call",call)
    ev.predict(tmp_path/"inputs.json",tmp_path/"model",None,True,1)
    def score(path):
        ev.score(tmp_path,tmp_path/"reference.json",path,tmp_path/"report.json")

    pred = tmp_path/"model/predictions.json"
    score(pred)
    stats = ev.read(tmp_path/"report.json")["by_split"]["holdout"]
    assert [stats[k] for k in ("human_decidable","model_correct","recovered","regressed",
            "invented_group","model_abstained","tables_recovered")] == [5,3,2,1,1,1,1]
    bad = ev.read(pred)
    bad["T"]["paths"]["C1"] = ["FOREIGN"]
    ev.write(tmp_path/"bad.json",bad)
    with pytest.raises(ValueError,match="Invalid model annotation"):
        score(tmp_path/"bad.json")


def test_unavailable_is_distinct_from_no_parent(tmp_path):
    ev.write(tmp_path/"inputs.json",{"t":{"targets":["C1"],"cells":[{"id":"C1"}]}})
    ev.predict(tmp_path/"inputs.json",tmp_path/"offline",None,False,0)
    p = ev.read(tmp_path/"offline/predictions.json")["t"]
    assert p["status"] == "not_run" and p["paths"] == {}

@pytest.mark.parametrize("covered",[True,False])
def test_nested_grid_preserves_caption_order_and_uncertain_source(covered):
    def table(cid,text):
        return {"cells":[dict(cell_id=cid, original_text=text, row_index=0,column_index=0,
                              rowspan=1,colspan=1,source_bbox={})]}
    tables = {"outer":table("owner","flattened value"),"inner":table("value","0.10")}
    links = {"containers":{"owner":{"covered":covered,"items":[{"kind":"text","text":"unit"},
            {"kind":"table","table":"inner"}]}},"parent_of":{"inner":"owner"}}
    cells = ev.review_cells("outer",tables,links)
    markup = ev.render_table(cells)
    assert markup.count("<table>") == 2
    assert markup.index("unit") < markup.index("<table>",1) < markup.index("0.10")
    assert ("flattened value" in markup) != covered
    assert cells[0]["contents"][1]["cells"][0]["id"] != cells[0]["id"]
