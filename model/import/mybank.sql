-- Starter model for the mybank benchmark schema.
-- One-time bootstrap input for:  ./bench model import model/import/mybank.sql
-- After import, model/mybank.yaml is the source of truth. Only declared foreign
-- keys become relations; undeclared (ERP style) joins are added in the YAML.

CREATE TABLE SEG_LKP (
    SEG_CD    char(2)      PRIMARY KEY,
    SEG_DESC  varchar(40)  NOT NULL
);

CREATE TABLE BRNCH (
    BRNCH_ID  integer      PRIMARY KEY,
    BRNCH_NM  varchar(60)  NOT NULL,
    CITY_NM   varchar(40),
    OPEN_DT   date
);

CREATE TABLE PROD_LKP (
    PROD_CD   varchar(6)   PRIMARY KEY,
    PROD_NM   varchar(60)  NOT NULL,
    PROD_TYP  char(1)      NOT NULL CHECK (PROD_TYP IN ('D', 'L', 'C'))
);

CREATE TABLE CUST_MSTR (
    CUST_ID   integer      PRIMARY KEY,
    CUST_NM   varchar(80)  NOT NULL,
    SEG_CD    char(2),
    OPEN_DT   date,
    STAT_CD   char(1)      DEFAULT 'A' CHECK (STAT_CD IN ('A', 'C', 'S'))
);

CREATE TABLE ACCT (
    ACCT_ID   bigint         PRIMARY KEY,
    CUST_ID   integer        NOT NULL REFERENCES CUST_MSTR (CUST_ID),
    BRNCH_ID  integer        NOT NULL,
    PROD_CD   varchar(6)     NOT NULL,
    OPEN_DT   date           NOT NULL,
    CLOSE_DT  date,
    CCY_CD    char(3)        NOT NULL DEFAULT 'EUR',
    CUR_BAL   numeric(14,2)  NOT NULL DEFAULT 0
);

ALTER TABLE ACCT ADD CONSTRAINT ACCT_BRNCH_FK FOREIGN KEY (BRNCH_ID) REFERENCES BRNCH (BRNCH_ID);

CREATE TABLE TXN (
    TXN_ID    bigint         PRIMARY KEY,
    ACCT_ID   bigint         NOT NULL,
    TXN_DT    date           NOT NULL,
    TXN_TYP   char(2)        NOT NULL CHECK (TXN_TYP IN ('DP', 'WD', 'FE', 'IN', 'TR')),
    TXN_AMT   numeric(12,2)  NOT NULL,
    DESC_TXT  varchar(120)
);

CREATE TABLE ACCT_BAL_MTH (
    ACCT_ID   bigint         NOT NULL REFERENCES ACCT (ACCT_ID),
    BAL_MTH   date           NOT NULL,
    BAL_AMT   numeric(14,2)  NOT NULL,
    PRIMARY KEY (ACCT_ID, BAL_MTH)
);

COMMENT ON TABLE SEG_LKP IS 'Customer segment lookup';
COMMENT ON TABLE BRNCH IS 'Bank branches';
COMMENT ON TABLE PROD_LKP IS 'Account product lookup';
COMMENT ON TABLE CUST_MSTR IS 'Customer master, one row per customer';
COMMENT ON COLUMN CUST_MSTR.CUST_NM IS 'Legal name';
COMMENT ON COLUMN CUST_MSTR.SEG_CD IS 'Segment code, see SEG_LKP (no foreign key)';
COMMENT ON COLUMN CUST_MSTR.STAT_CD IS 'A active, C closed, S suspended';
COMMENT ON TABLE ACCT IS 'Accounts, one customer owns many';
COMMENT ON COLUMN ACCT.PROD_CD IS 'Product code, see PROD_LKP (no foreign key)';
COMMENT ON COLUMN ACCT.CUR_BAL IS 'Current balance in account currency';
COMMENT ON TABLE TXN IS 'Account transactions; ACCT_ID has no foreign key, ERP style';
COMMENT ON COLUMN TXN.TXN_TYP IS 'DP deposit, WD withdrawal, FE fee, IN interest, TR transfer';
COMMENT ON TABLE ACCT_BAL_MTH IS 'Month-end balance per account';
