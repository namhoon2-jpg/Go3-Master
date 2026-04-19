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
    # 선생님께서 지정하신 2.5-flash 모델 유지
    model = genai.GenerativeModel('gemini-2.5-flash')
except:
    st.error("⚠️ Secrets 설정(GEMINI_API_KEY, GSHEET_SCRIPT_URL)이 누락되었습니다.")

if "analysis_result" not in st.session_state: st.session_state.analysis_result = ""
if "chat_history" not in st.session_state: st.session_state.chat_history = []

# ==========================================
# 2. 구글 시트 데이터 동기화 함수
# ==========================================
def sync_knowledge(new_content=None):
    try:
        if new_content:
            requests.post(GSHEET_SCRIPT_URL, json={"content": new_content})
        response = requests.get(GSHEET_SCRIPT_URL)
        return response.text if response.status_code == 200 else ""
    except:
        return ""

# ==========================================
# 3. 데이터 가공 함수 (성적 분석용)
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
st.set_page_config(page_title="고3 진학 마스터 V42", layout="wide")
st.title("🎓 고3 대입 전문 컨설팅 (V42 - 리포트 구조 강화)")

with st.sidebar:
    st.header("📋 학생 데이터 입력")
    target_major = st.text_input("희망 학과", placeholder="예: 의예과")
    excel_file = st.file_uploader("1. 성적 엑셀 (필수)", type=["xlsx"])
    pdf_file = st.file_uploader("2. 생기부 PDF (필수)", type="pdf")
    
    st.divider()
    st.header("📚 지식 데이터베이스 구축")
    ref_file = st.file_uploader("학습용 PDF/엑셀 업로드", type=["pdf", "xlsx"])
    
    if st.button("💾 이 파일을 영구 저장하기"):
        if ref_file:
            with st.spinner("모든 시트 정보 추출 중..."):
                extracted_text = f"\n[자료: {ref_file.name}]\n"
                if ref_file.name.endswith(".pdf"):
                    with pdfplumber.open(ref_file) as p: 
                        extracted_text += "".join([pg.extract_text() for pg in p.pages])
                else:
                    xls_ref = pd.ExcelFile(ref_file)
                    for sheet_name in xls_ref.sheet_names:
                        df_sheet = pd.read_excel(xls_ref, sheet_name=sheet_name)
                        extracted_text += f"\n--- 시트: {sheet_name} ---\n{df_sheet.to_string()}\n"
                sync_knowledge(extracted_text)
                st.success("데이터베이스 저장 완료!")

# ==========================================
# 5. 핵심 분석 프롬프트 (요청 사항 반영)
# ==========================================
if excel_file and pdf_file and target_major:
    if not st.session_state.analysis_result:
        with st.spinner('베테랑 교사의 시각으로 분석 중...'):
            i_df, m_df = process_performance_data(excel_file)
            with pdfplumber.open(pdf_file) as p: pdf_text = "".join([pg.extract_text() for pg in p.pages])
            accumulated_knowledge = sync_knowledge()

            prompt = f"""
            베테랑 입시 교사로서 {target_major} 지망 학생을 심층 분석함. 
            모든 답변은 개괄식(Bullet point)과 명사형 종결어미를 사용함.
            인사말은 생략하고 바로 [PART 1:]부터 시작함.

            [PART 1: 종합 진단]
            - 학생의 내신 및 모의고사 추이를 분석하여 수능 최저 충족 가능성과 전형 적합성을 매우 풍성하게 서술할 것.
            - 개괄식으로 항목을 나누어 가독성 있게 작성할 것.

            [PART 2: 대입 전략]
            - 지원 가능한 대학 라인(상향/적정/안정)을 구체적인 근거와 함께 풍성하게 제안함.
            - 추천 도서: 도서명과 간단한 추천 사유 위주로 간결하게 제시함.

            [PART 3: 심화 탐구 전략]
            - 총 3개의 심화 탐구 주제를 제시함.
            - 각 주제별 구성:
                1) 심화 탐구 주제: (구체적인 제목)
                2) 종적/횡적 근거: (생기부 기반의 연결성 분석)
                3) 주제 탐구 방법:
                   - Step 1: (기초 조사 단계)
                   - Step 2: (심화 분석/실험 단계)
                   - Step 3: (보고서/산출물 완성 단계)

            [PART 4: 면접 대비]
            - 각 예상 질문에 대해 아래 3단계로 구성할 것:
                1) 예상 질문: (학생의 활동 기반 질문)
                2) 모범 답안: (핵심 키워드가 포함된 답변 예시)
                3) 준비 방법: (추가로 공부하거나 답변을 보완할 지침)

            [태그] @PIE [교과:%, 정시:%, 종합:%] @ @PERCENT [00] @
            학생 데이터: 내신({i_df.to_string()}), 모의고사({m_df.to_string()})
            생기부 내용: {pdf_text[:15000]}
            누적 지식: {accumulated_knowledge[:10000]}
            """
            response = model.generate_content(prompt)
            st.session_state.analysis_result = response.text
            st.session_state.i_df, st.session_state.m_df = i_df, m_df

    # 탭 결과 출력
    tab1, tab2, tab3, tab4 = st.tabs(["📝 진단 및 전략", "🚀 심화 탐구 가이드", "💬 실시간 상담", "🖨️ 핵심 요약"])
    res = st.session_state.analysis_result
    
    if "[PART 1:" in res: main_content = "[PART 1:" + res.split("[PART 1:")[1]
    else: main_content = res

    clean_res = re.sub(r'@.*?@', '', main_content, flags=re.DOTALL)

    with tab1:
        st.subheader("📊 성적 및 전형 분석")
        c1, c2, c3 = st.columns(3)
        if not st.session_state.i_df.empty: c1.plotly_chart(px.line(st.session_state.i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="내신 등급"), use_container_width=True)
        if not st.session_state.m_df.empty: c2.plotly_chart(px.line(st.session_state.m_df, x="시험", y=["국어", "수학", "영어", "탐구"], markers=True, title="모의고사 추이", range_y=[0, 100]), use_container_width=True)
        
        pie_raw = re.search(r'@PIE \[(.*?)\] @', res)
        if pie_raw:
            p_data = [{"전형": k.strip(), "비중": int(re.sub(r'[^0-9]', '', v))} for k, v in [it.split(':') for it in pie_raw.group(1).split(',')]]
            c3.plotly_chart(px.pie(pd.DataFrame(p_data), values="비중", names="전형", hole=0.4, title="전형 적합도"), use_container_width=True)
        
        # PART 1 & 2 출력
        st.markdown(clean_res.split("[PART 3:")[0].replace("[PART 1:", "### 📝 [PART 1]").replace("[PART 2:", "### 🎯 [PART 2]"))

    with tab2:
        if "[PART 3:" in clean_res:
            p3_raw = clean_res.split("[PART 3:")[1].split("[PART 4:")[0]
            st.markdown("### 🚀 [PART 3] 생기부 기반 심화 탐구 로드맵")
            # 가시성 확보를 위한 포맷팅
            f_p3 = p3_raw.replace("심화 탐구 주제:", "#### 📍 주제:").replace("종적/횡적 근거:", "🔍 **근거:**").replace("주제 탐구 방법:", "🛠️ **주제 탐구 방법:**")
            st.markdown(f_p3)
            
            if "[PART 4:" in clean_res:
                st.markdown("---")
                st.markdown("### 🎤 [PART 4] 면접 예상 질문 및 가이드")
                f_p4 = clean_res.split("[PART 4:")[1].replace("예상 질문:", "#### ❓ 예상 질문:").replace("모범 답안:", "✅ **모범 답안:**").replace("준비 방법:", "🛠️ **준비 방법:**")
                st.markdown(f_p4)

    with tab3:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        if p_chat := st.chat_input("추가 질문을 입력하세요..."):
            st.session_state.chat_history.append({"role": "user", "content": p_chat})
            with st.chat_message("user"): st.markdown(p_chat)
            with st.chat_message("assistant"):
                ans = model.generate_content(f"배경 데이터: {res}\n질문: {p_chat}")
                st.markdown(ans.text)
                st.session_state.chat_history.append({"role": "assistant", "content": ans.text})

    with tab4:
        st.markdown("# 📋 상담 요약 리포트 (통합본)"); st.info(clean_res)
