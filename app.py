import streamlit as st
import google.generativeai as genai
import pandas as pd
import plotly.express as px
import pdfplumber
import requests
import re
import io

# ==========================================
# 1. 보안 및 API 설정
# ==========================================
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    GSHEET_SCRIPT_URL = st.secrets["GSHEET_SCRIPT_URL"]
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash')
except:
    st.error("⚠️ Secrets 설정 정보를 확인해주세요.")

if "analysis_result" not in st.session_state: st.session_state.analysis_result = ""
if "chat_history" not in st.session_state: st.session_state.chat_history = []

# ==========================================
# 2. 구글 시트 동기화 함수
# ==========================================
def sync_knowledge(new_content=None):
    try:
        if new_content:
            requests.post(GSHEET_SCRIPT_URL, json={"content": new_content})
        response = requests.get(GSHEET_SCRIPT_URL)
        return response.text if response.status_code == 200 else ""
    except: return ""

# ==========================================
# 3. 데이터 가공 (성적)
# ==========================================
def process_performance_data(file):
    xls = pd.ExcelFile(file)
    i_df, m_df = pd.DataFrame(), pd.DataFrame()
    if '학생부현황' in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name='학생부현황')
        res = []
        for g in [1.0, 2.0, 3.0]:
            for s_idx, (u_c, r_c) in enumerate([(3, 5), (10, 12)], 1):
                sub = df[df.iloc[:, 0] == g]
                u_s, w_s = 0, 0
                for _, row in sub.iterrows():
                    try:
                        u, r = float(row.iloc[u_c]), str(row.iloc[r_c]).strip()
                        m = re.search(r'^[1-9]', r)
                        if m: u_s += u; w_s += (u * float(m.group()))
                    except: continue
                if u_s > 0: res.append({"학기": f"{int(g)}-{s_idx}", "등급": round(w_s/u_s, 2)})
        i_df = pd.DataFrame(res)
    if '수능모의고사' in xls.sheet_names:
        df_m = pd.read_excel(xls, sheet_name='수능모의고사')
        m_res = []
        for _, row in df_m.iterrows():
            try:
                txt = str(row.iloc[0])
                g_m, d_m = re.search(r'(\d)학년', txt), re.search(r'\((\d{2})-(\d{2})\)', txt)
                if g_m and d_m:
                    eng = str(row.iloc[10]); eng_v = re.search(r'[1-9]', eng)
                    eng_s = 100 - (int(eng_v.group()) - 1) * 10 if eng_v else 0
                    m_res.append({
                        "key": int(f"{d_m.group(1)}{d_m.group(2)}"),
                        "시험": f"{g_m.group(1)}학년 {d_m.group(2)}월",
                        "국어": float(row.iloc[4]), "수학": float(row.iloc[8]), "영어": eng_s, "탐구": float(row.iloc[13])
                    })
            except: continue
        m_df = pd.DataFrame(m_res).sort_values("key").drop(columns="key") if m_res else pd.DataFrame()
    return i_df, m_df

# ==========================================
# 4. 메인 UI
# ==========================================
st.set_page_config(page_title="진학 마스터 V43", layout="wide")
st.title("🎓 고3 대입 전문 컨설팅 (V43 - 보수적 진단 강화형)")

with st.sidebar:
    st.header("📋 학생 데이터 입력")
    target_major = st.text_input("희망 학과", placeholder="예: 기계공학과")
    excel_file = st.file_uploader("1. 성적 엑셀", type=["xlsx"])
    pdf_file = st.file_uploader("2. 생기부 PDF", type="pdf")
    st.divider()
    st.header("📚 지식 데이터베이스")
    ref_file = st.file_uploader("학습용 파일 업로드", type=["pdf", "xlsx"])
    if st.button("💾 영구 저장"):
        if ref_file:
            with st.spinner("모든 시트 저장 중..."):
                extracted_text = f"\n[자료: {ref_file.name}]\n"
                if ref_file.name.endswith(".pdf"):
                    with pdfplumber.open(ref_file) as p: extracted_text += "".join([pg.extract_text() for pg in p.pages])
                else:
                    xls_ref = pd.ExcelFile(ref_file)
                    for s in xls_ref.sheet_names:
                        extracted_text += f"\n--- 시트: {s} ---\n{pd.read_excel(xls_ref, s).to_string()}\n"
                sync_knowledge(extracted_text); st.success("저장 완료!")

# ==========================================
# 5. 보수적 분석 프롬프트 (최종 튜닝)
# ==========================================
if excel_file and pdf_file and target_major:
    if not st.session_state.analysis_result:
        with st.spinner('지방 일반고 현실을 반영하여 보수적으로 분석 중...'):
            i_df, m_df = process_performance_data(excel_file)
            with pdfplumber.open(pdf_file) as p: pdf_text = "".join([pg.extract_text() for pg in p.pages])
            k_base = sync_knowledge()

            prompt = f"""
            지방 일반고 전문 입시 컨설턴트로서 {target_major} 지망 학생을 분석함.
            답변은 명사형 종결어미의 개괄식(Bullet point)으로 작성함.
            인사말 생략, [PART 1:]부터 바로 시작함.

            [지침: 지방 일반고 특성 반영]
            - 내신 등급을 최우선으로 하는 '교과 전형' 중심의 보수적 라인을 제안할 것.
            - 종합 전형은 학생의 생기부가 '매우 우수'할 때만 적정으로 분류하고, 보통은 '상향'으로 간주할 것.
            - 수능 최저학력기준은 모의고사보다 실제 수능에서 하락할 가능성을 염두에 두고 매우 엄격하게 잣대를 적용할 것.

            [PART 1: 종합 진단]
            - 내신 및 모의고사 추이를 분석하여 수능 최저 충족 및 전형 적합성을 '방어적' 관점에서 풍성하게 서술함.

            [PART 2: 대입 전략]
            - 대학 라인(상향/적정/안정)을 제안하되, 안정 라인을 반드시 포함할 것.
            - 추천 도서: 도서명과 간단한 추천 사유 제시.

            [PART 3: 심화 탐구 전략]
            - 3개 주제 제안 (주제명, 종적/횡적 근거 포함)
            - 주제 탐구 방법: Step 1, 2, 3 단계별 가이드라인 제시.

            [PART 4: 면접 대비]
            - 3단계 구성 (예상 질문 - 모범 답안 - 준비 방법)

            [태그] @PIE [교과:%, 정시:%, 종합:%] @ @PERCENT [00] @
            학생: 내신({i_df.to_string()}), 모의고사({m_df.to_string()})
            생기부: {pdf_text[:15000]}
            누적지식: {k_base[:10000]}
            """
            response = model.generate_content(prompt)
            st.session_state.analysis_result = response.text
            st.session_state.i_df, st.session_state.m_df = i_df, m_df

    # 결과 출력
    tab1, tab2, tab3, tab4 = st.tabs(["📝 진단 및 전략", "🚀 심화 탐구 가이드", "💬 실시간 상담", "🖨️ 핵심 요약"])
    res = st.session_state.analysis_result
    main_content = "[PART 1:" + res.split("[PART 1:")[1] if "[PART 1:" in res else res
    clean_res = re.sub(r'@.*?@', '', main_content, flags=re.DOTALL)

    with tab1:
        st.subheader("📊 성적 및 보수적 전형 분석")
        c1, c2, c3 = st.columns(3)
        if not st.session_state.i_df.empty: c1.plotly_chart(px.line(st.session_state.i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="내신 등급 추이"), use_container_width=True)
        if not st.session_state.m_df.empty: c2.plotly_chart(px.line(st.session_state.m_df, x="시험", y=["국어", "수학", "영어", "탐구"], markers=True, title="모의고사 추이", range_y=[0, 100]), use_container_width=True)
        pie_raw = re.search(r'@PIE \[(.*?)\] @', res)
        if pie_raw:
            p_data = [{"전형": k.strip(), "비중": int(re.sub(r'[^0-9]', '', v))} for k, v in [it.split(':') for it in pie_raw.group(1).split(',')]]
            c3.plotly_chart(px.pie(pd.DataFrame(p_data), values="비중", names="전형", hole=0.4, title="추천 전형 비중"), use_container_width=True)
        st.markdown(clean_res.split("[PART 3:")[0].replace("[PART 1:", "### 📝 [PART 1]").replace("[PART 2:", "### 🎯 [PART 2]"))

    with tab2:
        if "[PART 3:" in clean_res:
            p3_raw = clean_res.split("[PART 3:")[1].split("[PART 4:")[0]
            st.markdown("### 🚀 [PART 3] 생기부 기반 심화 탐구 로드맵")
            st.markdown(p3_raw.replace("심화 탐구 주제:", "#### 📍 주제:").replace("종적/횡적 근거:", "🔍 **근거:**").replace("주제 탐구 방법:", "🛠️ **주제 탐구 방법:**"))
            if "[PART 4:" in clean_res:
                st.markdown("---")
                st.markdown("### 🎤 [PART 4] 면접 예상 질문 및 가이드")
                st.markdown(clean_res.split("[PART 4:")[1].replace("예상 질문:", "#### ❓ 질문:").replace("모범 답안:", "✅ **모범 답안:**").replace("준비 방법:", "🛠️ **준비 방법:**"))

    with tab3:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        if p_chat := st.chat_input("추가 질문..."):
            st.session_state.chat_history.append({"role": "user", "content": p_chat})
            with st.chat_message("user"): st.markdown(p_chat)
            with st.chat_message("assistant"):
                ans = model.generate_content(f"배경: {res}\n질문: {p_chat}")
                st.markdown(ans.text); st.session_state.chat_history.append({"role": "assistant", "content": ans.text})

    with tab4:
        st.markdown("# 📋 상담 요약 리포트"); st.info(clean_res)
