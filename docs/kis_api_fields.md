# KIS Developers 국내주식 주문 API 필드 (공식 문서 발췌)

> 출처: KIS Developers 포털(apiportal.koreainvestment.com) [국내주식] 주문/계좌 3개 API의 표를 사용자가 복사해 정리한 것.
> **이 파일에 적힌 사실만 근거로 쓴다.** 파일에 없는 필드·TR_ID·값은 추측하지 말 것.
> 예시 값(계좌번호, 전화번호 등)은 제거했다. 문서 작성일/버전은 미확인 → 포털에서 변경될 수 있다.

공통 도메인
- 실전: `https://openapi.koreainvestment.com:9443`
- 모의: `https://openapivts.koreainvestment.com:29443`

공통 요청 헤더(필수 `Y`): `content-type`(application/json; charset=utf-8), `authorization`(`Bearer {토큰}`), `appkey`, `appsecret`, `tr_id`, `custtype`(P=개인, B=법인)

---

## 1. 주식주문(현금) — API ID `v1_국내주식-001`

- 메서드/URL: `POST /uapi/domestic-stock/v1/trading/order-cash`
- 실전 TR_ID: 매도 `TTTC0011U` / 매수 `TTTC0012U`
- 모의 TR_ID: 매도 `VTTC0011U` / 매수 `VTTC0012U`
- **구 TR_ID(문서가 "사전 고지 없이 막힐 수 있음, 반드시 신TR로 변경"이라 명시)**

| 구분 | 구 TR_ID | 신 TR_ID |
|---|---|---|
| 실전 매도 | TTTC0801U | TTTC0011U |
| 모의 매도 | VTTC0801U | VTTC0011U |
| 실전 매수 | TTTC0802U | TTTC0012U |
| 모의 매수 | VTTC0802U | VTTC0012U |

- 문서 개요: `ORD_QTY`, `ORD_UNPR` 등은 **문자열(String)** 로 전달. POST body의 key는 **대문자**. `ORD_UNPR`가 없는 주문은 상한가로 주문금액을 잡고 체결 후 체결금액으로 정산. 매수는 `TTTC0012U`로 미수매수 가능(증거금40% 계좌 한정).

### Request Body

| Element | 한글명 | Type | 필수 | 길이 | 설명 |
|---|---|---|---|---|---|
| CANO | 종합계좌번호 | string | Y | 8 | |
| ACNT_PRDT_CD | 계좌상품코드 | string | Y | 2 | 상품유형코드 |
| PDNO | 상품번호 | string | Y | 12 | 종목코드 6자리 (ETN은 7자리) |
| SLL_TYPE | 매도유형 | string | N | 2 | 01 일반매도 / 02 임의매매 / 05 대차매도 (미입력 시 01) |
| ORD_DVSN | 주문구분 | string | Y | 2 | **[KRX] 00 지정가, 01 시장가**, 02 조건부지정가, 03 최유리지정가, 04 최우선지정가, 05 장전 시간외, 06 장후 시간외, 11 IOC지정가, 12 FOK지정가, 13 IOC시장가, 14 FOK시장가, 15 IOC최유리, 16 FOK최유리, 21 중간가, 22 스톱지정가, 23 중간가IOC, 24 중간가FOK, 41~47 KRX애프터마켓 계열 |
| ORD_QTY | 주문수량 | string | Y | 10 | |
| ORD_UNPR | 주문단가 | string | Y | 19 | 시장가 등 주문 시 `"0"` |
| CNDT_PRIC | 조건가격 | string | N | 19 | 스톱지정가(ORD_DVSN=22)에서만 필수 |
| EXCG_ID_DVSN_CD | 거래소ID구분코드 | string | N | 3 | KRX / NXT / SOR. 미입력 시 KRX, **모의투자는 KRX만 가능** |

### Response Body

| Element | 한글명 | Type | 설명 |
|---|---|---|---|
| rt_cd | 성공 실패 여부 | string | |
| msg_cd | 응답코드 | string | |
| msg1 | 응답메세지 | string | |
| output | 응답상세 | object | 아래 3개 |
| output.KRX_FWDG_ORD_ORGNO | 계좌관리점코드 | string(5) | 대문자 키 |
| output.**ODNO** | **주문번호** | string(10) | 대문자 키 |
| output.ORD_TMD | 주문시간 | string(6) | |

Response Example (문서 원문)
```json
{
  "rt_cd": "0",
  "msg_cd": "APBK0013",
  "msg1": "주문 전송 완료 되었습니다.",
  "output": {
    "KRX_FWDG_ORD_ORGNO": "06010",
    "ODNO": "0001569157",
    "ORD_TMD": "155211"
  }
}
```

---

## 2. 주식일별주문체결조회 — API ID `v1_국내주식-005`

- 메서드/URL: `GET /uapi/domestic-stock/v1/trading/inquire-daily-ccld`
- 실전 TR_ID: 3개월 이내 `TTTC0081R` / 3개월 이전 `CTSC9215R`
- 모의 TR_ID: 3개월 이내 `VTTC0081R` / 3개월 이전 `VTSC9215R`
- 구 TR_ID → 신: 실전 3개월 이내 TTTC8001R→TTTC0081R, 3개월 이전 CTSC9115R→CTSC9215R / 모의 3개월 이내 VTTC8001R→VTTC0081R, 3개월 이전 VTSC9115R→VTSC9215R
- **한 번 호출 최대 건수: 실전 100건, 모의 15건. 이후는 연속조회.**
- 3개월 이전 조회는 장중에 DB 지연이 있을 수 있어 장 종료 후(15:30 이후), 조회 기간을 짧게 권장.
- 요청 헤더 `tr_cont`: 공백=초기 조회, `N`=다음 데이터 조회(응답 헤더 tr_cont가 M일 때).
- 응답 헤더 `tr_cont`: `F` 또는 `M`=다음 데이터 있음, `D` 또는 `E`=마지막 데이터.

### Request Query Parameter (전부 필수 Y 표기, 일부는 빈 값 허용으로 보이나 문서에 명시 없음)

| Element | 한글명 | 길이 | 설명 |
|---|---|---|---|
| CANO | 종합계좌번호 | 8 | 계좌번호 앞 8자리 |
| ACNT_PRDT_CD | 계좌상품코드 | 2 | 계좌번호 뒤 2자리 |
| INQR_STRT_DT | 조회시작일자 | 8 | YYYYMMDD |
| INQR_END_DT | 조회종료일자 | 8 | YYYYMMDD |
| SLL_BUY_DVSN_CD | 매도매수구분코드 | 2 | 00 전체 / 01 매도 / 02 매수 |
| PDNO | 상품번호 | 12 | 종목번호 6자리 (필수 N) |
| ORD_GNO_BRNO | 주문채번지점번호 | 5 | 주문 시 시스템이 지정한 영업점코드 (표기 Y, 예시는 빈 문자열) |
| ODNO | 주문번호 | 10 | 필수 N |
| CCLD_DVSN | 체결구분 | 2 | 00 전체 / 01 체결 / 02 미체결 |
| INQR_DVSN | 조회구분 | 2 | 00 역순 / 01 정순 |
| INQR_DVSN_1 | 조회구분1 | 1 | 없음 전체 / 1 ELW / 2 프리보드 |
| INQR_DVSN_3 | 조회구분3 | 2 | 00 전체 / 01 현금 / 02 신용 / 03 담보 / 04 대주 / 05 대여 / 06 자기융자신규·상환 / 07 유통융자신규·상환 |
| EXCG_ID_DVSN_CD | 거래소ID구분코드 | 3 | KRX / NXT / SOR / ALL. **모의투자는 KRX만 제공** (표에는 Y로 표기되나 Request Example에는 없음) |
| CTX_AREA_FK100 | 연속조회검색조건100 | 100 | 최초 조회 시 공란, 다음 페이지는 이전 응답 값 |
| CTX_AREA_NK100 | 연속조회키100 | 100 | 최초 조회 시 공란, 다음 페이지는 이전 응답 값 |

Request Example (문서 원문, 계좌번호는 가짜값)
```json
{
  "CANO": "12345678", "ACNT_PRDT_CD": "01",
  "INQR_STRT_DT": "20211101", "INQR_END_DT": "20211101",
  "SLL_BUY_DVSN_CD": "00", "INQR_DVSN": "00", "PDNO": "",
  "CCLD_DVSN": "00", "ORD_GNO_BRNO": "", "ODNO": "",
  "INQR_DVSN_3": "00", "INQR_DVSN_1": "",
  "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
}
```

### Response Body

공통: `rt_cd`, `msg_cd`, `msg1`, 연속조회용 `ctx_area_fk100`, `ctx_area_nk100`(예시에서 확인됨, 표에는 없음)

**output1 (배열, 소문자 키)** — 봇이 쓸 후보 필드 (전체는 문서 표 참조)

| Element | 한글명 | 비고 |
|---|---|---|
| ord_dt | 주문일자 | 8 |
| ord_gno_brno | 주문채번지점번호 | 5 |
| **odno** | **주문번호** | 10 |
| orgn_odno | 원주문번호 | 10 |
| sll_buy_dvsn_cd | 매도매수구분코드 | 예시: 02=매수 |
| sll_buy_dvsn_cd_name | 매도매수구분코드명 | 예시 값: `BUY REJECT` |
| pdno | 상품번호 | |
| ord_qty | 주문수량 | |
| ord_unpr | 주문단가 | |
| ord_tmd | 주문시각 | |
| **tot_ccld_qty** | **총체결수량** | |
| **avg_prvs** | **평균가** | |
| **cncl_yn** | **취소여부** | 예시에서 빈 문자열 |
| tot_ccld_amt | 총체결금액 | |
| cncl_cfrm_qty | 취소확인수량 | |
| **rmn_qty** | **잔여수량** | |
| **rjct_qty** | **거부수량** | |
| ord_dvsn_cd | 주문구분코드 | |

그 외 필드(전체): ord_dvsn_name, prdt_name, loan_dt, ordr_empno, ccld_cndt_name, inqr_ip_addr, cpbc_ordp_ord_rcit_dvsn_cd, cpbc_ordp_infm_mthd_dvsn_cd, infm_tmd, ctac_tlno, prdt_type_cd, excg_dvsn_cd, cpbc_ordp_mtrl_dvsn_cd, ord_orgno, rsvn_ord_end_dt, excg_id_dvsn_Cd, stpm_cndt_pric, stpm_efct_occr_dtmd

**output2 (객체)**: tot_ord_qty, tot_ccld_qty, tot_ccld_amt, prsm_tlex_smtl, pchs_avg_pric

> **문서 내 불일치(그대로 옮김)**: output2의 한글명이 `tot_ccld_amt`=매입평균가격, `prsm_tlex_smtl`=총체결금액, `pchs_avg_pric`=추정제비용합계로 **필드명과 한글명이 어긋나 보인다.** output2는 신뢰하지 말고 **output1 행 단위 값만 사용**할 것. 키 대소문자도 표(`excg_id_dvsn_Cd`)와 예시가 다를 수 있다.

Response Example (문서 원문 일부, 전화번호 제거)
```json
{
  "ctx_area_fk100": "12345678^01^20220103^20220103^ ^00^00^",
  "ctx_area_nk100": "",
  "output1": [
    {
      "ord_dt": "20220103", "ord_gno_brno": "06010", "odno": "0001568197",
      "orgn_odno": "", "ord_dvsn_name": "Limit",
      "sll_buy_dvsn_cd": "02", "sll_buy_dvsn_cd_name": "BUY REJECT",
      "pdno": "009150", "prdt_name": "삼성전기",
      "ord_qty": "10", "ord_unpr": "150000", "ord_tmd": "170100",
      "tot_ccld_qty": "0", "avg_prvs": "0", "cncl_yn": "",
      "tot_ccld_amt": "0", "cncl_cfrm_qty": "0", "rmn_qty": "0", "rjct_qty": "10",
      "ord_dvsn_cd": "00", "prdt_type_cd": "300", "excg_dvsn_cd": "02",
      "ord_orgno": "00000"
    }
  ],
  "output2": {
    "tot_ord_qty": "281", "tot_ccld_qty": "0", "tot_ccld_amt": "0",
    "prsm_tlex_smtl": "0", "pchs_avg_pric": "0.0000"
  },
  "rt_cd": "0",
  "msg_cd": "KIOK0510",
  "msg1": "조회가 완료되었습니다"
}
```

---

## 3. 주식주문(정정취소) — API ID `v1_국내주식-003`

- 메서드/URL: `POST /uapi/domestic-stock/v1/trading/order-rvsecncl`
- 실전 TR_ID `TTTC0013U` / 모의 TR_ID `VTTC0013U`
- 구 TR_ID: 실전 `TTTC0803U` → `TTTC0013U`, 모의 `VTTC0803U` → `VTTC0013U`
- 개요: 이미 체결된 건은 정정·취소 불가. 정정 가능 수량은 원주문 수량 초과 불가. 문서는 **호출 전에 "주식정정취소가능주문조회"로 정정취소가능수량(psbl_qty)을 확인**하라고 권고 (그 API 문서는 이 파일에 없음).
- POST body key는 대문자.

### Request Body

| Element | 한글명 | Type | 필수 | 길이 | 설명 |
|---|---|---|---|---|---|
| CANO | 종합계좌번호 | string | Y | 8 | |
| ACNT_PRDT_CD | 계좌상품코드 | string | Y | 2 | |
| KRX_FWDG_ORD_ORGNO | 한국거래소전송주문조직번호 | string | Y | 5 | (설명 없음. 주문 응답의 KRX_FWDG_ORD_ORGNO와 같은 이름) |
| ORGN_ODNO | 원주문번호 | string | Y | 10 | |
| ORD_DVSN | 주문구분 | string | Y | 2 | 주문(현금)과 동일한 코드표 |
| **RVSE_CNCL_DVSN_CD** | 정정취소구분코드 | string | Y | 2 | **01 정정 / 02 취소** |
| ORD_QTY | 주문수량 | string | Y | 10 | |
| ORD_UNPR | 주문단가 | string | Y | 19 | |
| **QTY_ALL_ORD_YN** | 잔량전부주문여부 | string | Y | 1 | **Y 전량 / N 일부** |
| CNDT_PRIC | 조건가격 | string | N | 19 | 스탑지정가에서 사용 |
| EXCG_ID_DVSN_CD | 거래소ID구분코드 | string | N | 3 | 미입력 시 KRX, 모의는 KRX만 |

Request Example (문서 원문 — **정정(01) 예시**이며 취소 예시는 문서에 없음)
```json
{
  "CANO": "810XXXXX", "ACNT_PRDT_CD": "01",
  "KRX_FWDG_ORD_ORGNO": "", "ORGN_ODNO": "0001566017",
  "ORD_DVSN": "00", "RVSE_CNCL_DVSN_CD": "01",
  "ORD_QTY": "1", "ORD_UNPR": "180000", "QTY_ALL_ORD_YN": "N"
}
```

### Response Body

| Element | 한글명 | 설명 |
|---|---|---|
| rt_cd / msg_cd / msg1 | | |
| output.krx_fwdg_ord_orgno | 한국거래소전송주문조직번호 | 표에는 소문자, **예시에는 대문자 `KRX_FWDG_ORD_ORGNO`** |
| output.odno | 주문번호 | 표에는 소문자, **예시에는 대문자 `ODNO`** |
| output.ord_tmd | 주문시각 | |

---

## 문서 내 대소문자 불일치 (그대로 기록)

| API | 표의 키 | 예시의 키 |
|---|---|---|
| 주문(현금) 응답 | 대문자(`ODNO`) | 대문자(`ODNO`) |
| 정정취소 응답 | 소문자(`odno`) | **대문자(`ODNO`)** |
| 일별체결조회 output1 | 소문자 | 소문자 |

→ 구현은 응답 키를 **대소문자 구분 없이** 읽는 것이 안전하다고 보이나, 이는 해석이며 문서의 직접 근거는 아니다.

## 이 파일에 없는 것 (추측 금지)

- 해외주식 주문·체결조회·정정취소 API
- 주식정정취소가능주문조회 API
- **ETF 호가 단위표**
- 취소(02) 요청 시 `ORD_QTY`, `ORD_UNPR`에 넣을 값의 규칙
- 모의 서버의 실제 체결 정책(즉시 체결 여부, 시간 제한)
