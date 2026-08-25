$version: "2.0"

namespace vendor

use alloy#dataExamples
use alloy#dateFormat
use alloy.openapi#openapiExtensions
use smithytranslate#contentType
use smithytranslate#contentTypeDiscriminated

service VendorService {
    operations: [
        GetStocksV1Dividends
        GetStocksV1Splits
        ListTickers
    ]
}

/// Contains historical dividend payment records for US stocks with split-adjusted amounts and historical adjustment factors for price normalization.
@http(
    method: "GET"
    uri: "/stocks/v1/dividends"
    code: 200
)
@tags([
    "us_stocks_reference"
])
operation GetStocksV1Dividends {
    input: GetStocksV1DividendsInput
    output: GetStocksV1Dividends200
    errors: [
        GetStocksV1Dividends400
    ]
}

/// Contains historical stock split and reverse split events for US equities with historical adjustment factors for price normalization.
@http(
    method: "GET"
    uri: "/stocks/v1/splits"
    code: 200
)
@tags([
    "us_stocks_reference"
])
operation GetStocksV1Splits {
    input: GetStocksV1SplitsInput
    output: GetStocksV1Splits200
    errors: [
        GetStocksV1Splits400
    ]
}

/// Query all ticker symbols which are supported by Massive. This API currently includes Stocks/Equities, Indices, Forex, and Crypto.
@openapiExtensions(
    "x-polygon-paginate": {
        sort: {
            enum: [
                "ticker"
                "name"
                "market"
                "locale"
                "primary_exchange"
                "type"
                "currency_symbol"
                "currency_name"
                "base_currency_symbol"
                "base_currency_name"
                "cik"
                "composite_figi"
                "share_class_figi"
                "last_updated_utc"
                "delisted_utc"
            ]
            default: "ticker"
        }
        limit: {
            max: 1000
            default: 100
        }
    }
    "x-polygon-entitlement-data-type": {
        name: "reference"
        description: "Reference data"
    }
)
@http(
    method: "GET"
    uri: "/v3/reference/tickers"
    code: 200
)
@tags([
    "reference:tickers:list"
])
operation ListTickers {
    input: ListTickersInput
    output: ListTickers200
    errors: [
        ListTickers401
    ]
}

structure ApplicationJson {
    /// The total number of results for this request.
    count: Integer
    /// If present, this value can be used to fetch the next page of data.
    next_url: String
    /// A request id assigned by the server.
    request_id: String
    results: ListTickers200BodyApplicationJsonResults
    /// The status of this request's response.
    status: String
}

structure GetStocksV1Dividends200 {
    @httpPayload
    @required
    @contentType("application/json")
    body: GetStocksV1Dividends200Body
}

structure GetStocksV1Dividends200Body {
    /// If present, this value can be used to fetch the next page.
    next_url: String
    /// A request id assigned by the server.
    @required
    request_id: String
    @required
    results: GetStocksV1Dividends200BodyResults
    @required
    status: GetStocksV1Dividends200BodyStatus
}

structure GetStocksV1Dividends200BodyResultsItem {
    /// Original dividend amount per share in the specified currency
    cash_amount: Double
    /// Currency code for the dividend payment (e.g., USD, CAD)
    currency: String
    /// Date when the company officially announced the dividend
    @dateFormat
    declaration_date: String
    /// Classification describing the nature of this dividend's recurrence pattern: recurring (paid on a regular schedule), special (one-time or commemorative), supplemental (extra beyond the regular schedule), irregular (unpredictable or non-recurring), unknown (cannot be classified from available data)
    @required
    distribution_type: String
    /// Date when the stock begins trading without the dividend value
    @dateFormat
    ex_dividend_date: String
    /// How many times per year this dividend is expected to occur. A value of 0 means the distribution is non-recurring or irregular (e.g., special, supplemental, or a one-off dividend). Other possible values include 1 (annual), 2 (semi-annual), 3 (trimester), 4 (quarterly), 12 (monthly), 24 (bi-monthly), 52 (weekly), 104 (bi-weekly), and 365 (daily) depending on the issuer's declared or inferred payout cadence.
    frequency: Long
    /// Cumulative adjustment factor used to offset dividend effects on historical prices. To adjust a historical price for dividends: for a price on date D, find the first dividend whose `ex_dividend_date` is after date D and multiply the price by that dividend's `historical_adjustment_factor`.
    historical_adjustment_factor: Double
    /// Unique identifier for each dividend record
    id: String
    /// Date when the dividend payment is distributed to shareholders
    @dateFormat
    pay_date: String
    /// Date when shareholders must be on record to be eligible for the dividend payment
    @dateFormat
    record_date: String
    /// Dividend amount adjusted for stock splits that occurred after the dividend was paid, expressed on a current share basis
    split_adjusted_cash_amount: Double
    /// Stock symbol for the company issuing the dividend
    ticker: String
}

@error("client")
@httpError(400)
structure GetStocksV1Dividends400 {
    @httpPayload
    @required
    @contentType("application/json")
    body: GetStocksV1Dividends400Body
}

structure GetStocksV1Dividends400Body {
    /// A message describing the source of the error.
    @required
    error: String
    /// A request id assigned by the server.
    @required
    request_id: String
    @required
    status: GetStocksV1Dividends400BodyStatus
}

structure GetStocksV1DividendsInput {
    /// Stock symbol for the company issuing the dividend
    @httpQuery("ticker")
    ticker: String
    /// Filter equal to any of the values. Multiple values can be specified by using a comma separated list.
    @httpQuery("ticker.any_of")
    tickerany_of: String
    /// Filter greater than the value.
    @httpQuery("ticker.gt")
    tickergt: String
    /// Filter greater than or equal to the value.
    @httpQuery("ticker.gte")
    tickergte: String
    /// Filter less than the value.
    @httpQuery("ticker.lt")
    tickerlt: String
    /// Filter less than or equal to the value.
    @httpQuery("ticker.lte")
    tickerlte: String
    /// Date when the stock begins trading without the dividend value Value must be formatted 'yyyy-mm-dd'.
    @httpQuery("ex_dividend_date")
    ex_dividend_date: String
    /// Filter greater than the value. Value must be formatted 'yyyy-mm-dd'.
    @httpQuery("ex_dividend_date.gt")
    ex_dividend_dategt: String
    /// Filter greater than or equal to the value. Value must be formatted 'yyyy-mm-dd'.
    @httpQuery("ex_dividend_date.gte")
    ex_dividend_dategte: String
    /// Filter less than the value. Value must be formatted 'yyyy-mm-dd'.
    @httpQuery("ex_dividend_date.lt")
    ex_dividend_datelt: String
    /// Filter less than or equal to the value. Value must be formatted 'yyyy-mm-dd'.
    @httpQuery("ex_dividend_date.lte")
    ex_dividend_datelte: String
    /// How many times per year this dividend is expected to occur. A value of 0 means the distribution is non-recurring or irregular (e.g., special, supplemental, or a one-off dividend). Other possible values include 1 (annual), 2 (semi-annual), 3 (trimester), 4 (quarterly), 12 (monthly), 24 (bi-monthly), 52 (weekly), 104 (bi-weekly), and 365 (daily) depending on the issuer's declared or inferred payout cadence. Value must be an integer.
    @httpQuery("frequency")
    frequency: Long
    /// Filter greater than the value. Value must be an integer.
    @httpQuery("frequency.gt")
    frequencygt: Long
    /// Filter greater than or equal to the value. Value must be an integer.
    @httpQuery("frequency.gte")
    frequencygte: Long
    /// Filter less than the value. Value must be an integer.
    @httpQuery("frequency.lt")
    frequencylt: Long
    /// Filter less than or equal to the value. Value must be an integer.
    @httpQuery("frequency.lte")
    frequencylte: Long
    /// Classification describing the nature of this dividend's recurrence pattern: recurring (paid on a regular schedule), special (one-time or commemorative), supplemental (extra beyond the regular schedule), irregular (unpredictable or non-recurring), unknown (cannot be classified from available data)
    @httpQuery("distribution_type")
    distribution_type: DistributionType
    /// Filter equal to any of the values. Multiple values can be specified by using a comma separated list.
    @httpQuery("distribution_type.any_of")
    distribution_typeany_of: DistributionTypeAnyOf
    /// Limit the maximum number of results returned. Defaults to '100' if not specified. The maximum allowed limit is '5000'.
    @httpQuery("limit")
    @range(
        min: 1
        max: 5000
    )
    limit: Integer
    /// A comma separated list of sort columns. For each column, append '.asc' or '.desc' to specify the sort direction. The sort column defaults to 'ticker' if not specified. The sort order defaults to 'asc' if not specified.
    @httpQuery("sort")
    sort: String
}

structure GetStocksV1Splits200 {
    @httpPayload
    @required
    @contentType("application/json")
    body: GetStocksV1Splits200Body
}

structure GetStocksV1Splits200Body {
    /// If present, this value can be used to fetch the next page.
    next_url: String
    /// A request id assigned by the server.
    @required
    request_id: String
    @required
    results: GetStocksV1Splits200BodyResults
    @required
    status: GetStocksV1Splits200BodyStatus
}

structure GetStocksV1Splits200BodyResultsItem {
    /// Classification of the share-change event. Possible values include: forward_split (share count increases), reverse_split (share count decreases), stock_dividend (shares issued as a dividend)
    @required
    adjustment_type: String
    /// Date when the stock split takes effect. The adjustment is applied overnight. On the prior trading day, the post-market session is the last session that shows pre-split prices. On the execution date, all trading is already adjusted for the split. This includes the pre-market session.
    @dateFormat
    execution_date: String
    /// Cumulative adjustment factor used to offset split effects on historical prices. To adjust a historical price for splits: for a price on date D, find the first split whose `execution_date` is after date D and multiply the unadjusted price by the `historical_adjustment_factor`.
    historical_adjustment_factor: Double
    /// Unique identifier for each stock split event
    id: String
    /// Denominator of the split ratio (old shares)
    split_from: Double
    /// Numerator of the split ratio (new shares)
    split_to: Double
    /// Stock symbol for the company that executed the split
    ticker: String
}

@error("client")
@httpError(400)
structure GetStocksV1Splits400 {
    @httpPayload
    @required
    @contentType("application/json")
    body: GetStocksV1Splits400Body
}

structure GetStocksV1Splits400Body {
    /// A message describing the source of the error.
    @required
    error: String
    /// A request id assigned by the server.
    @required
    request_id: String
    @required
    status: GetStocksV1Splits400BodyStatus
}

structure GetStocksV1SplitsInput {
    /// Stock symbol for the company that executed the split
    @httpQuery("ticker")
    ticker: String
    /// Filter equal to any of the values. Multiple values can be specified by using a comma separated list.
    @httpQuery("ticker.any_of")
    tickerany_of: String
    /// Filter greater than the value.
    @httpQuery("ticker.gt")
    tickergt: String
    /// Filter greater than or equal to the value.
    @httpQuery("ticker.gte")
    tickergte: String
    /// Filter less than the value.
    @httpQuery("ticker.lt")
    tickerlt: String
    /// Filter less than or equal to the value.
    @httpQuery("ticker.lte")
    tickerlte: String
    /// Date when the stock split takes effect. The adjustment is applied overnight. On the prior trading day, the post-market session is the last session that shows pre-split prices. On the execution date, all trading is already adjusted for the split. This includes the pre-market session. Value must be formatted 'yyyy-mm-dd'.
    @httpQuery("execution_date")
    execution_date: String
    /// Filter greater than the value. Value must be formatted 'yyyy-mm-dd'.
    @httpQuery("execution_date.gt")
    execution_dategt: String
    /// Filter greater than or equal to the value. Value must be formatted 'yyyy-mm-dd'.
    @httpQuery("execution_date.gte")
    execution_dategte: String
    /// Filter less than the value. Value must be formatted 'yyyy-mm-dd'.
    @httpQuery("execution_date.lt")
    execution_datelt: String
    /// Filter less than or equal to the value. Value must be formatted 'yyyy-mm-dd'.
    @httpQuery("execution_date.lte")
    execution_datelte: String
    /// Classification of the share-change event. Possible values include: forward_split (share count increases), reverse_split (share count decreases), stock_dividend (shares issued as a dividend)
    @httpQuery("adjustment_type")
    adjustment_type: AdjustmentType
    /// Filter equal to any of the values. Multiple values can be specified by using a comma separated list.
    @httpQuery("adjustment_type.any_of")
    adjustment_typeany_of: AdjustmentTypeAnyOf
    /// Limit the maximum number of results returned. Defaults to '100' if not specified. The maximum allowed limit is '5000'.
    @httpQuery("limit")
    @range(
        min: 1
        max: 5000
    )
    limit: Integer
    /// A comma separated list of sort columns. For each column, append '.asc' or '.desc' to specify the sort direction. The sort column defaults to 'execution_date' if not specified. The sort order defaults to 'desc' if not specified.
    @httpQuery("sort")
    sort: String
}

structure ListTickers200 {
    @httpPayload
    @required
    body: ListTickers200Body
}

@openapiExtensions(
    "x-polygon-go-type": {
        name: "ReferenceTicker"
        path: "github.com/polygon-io/go-lib-models/v2/globals"
    }
)
structure ListTickers200BodyApplicationJsonResultsItem {
    /// Whether or not the asset is actively traded. False means the asset has been delisted.
    active: Boolean
    /// The name of the currency that this asset is priced against.
    base_currency_name: String
    /// The ISO 4217 code of the currency that this asset is priced against.
    base_currency_symbol: String
    /// The CIK number for this ticker. Find more information [here](https://en.wikipedia.org/wiki/Central_Index_Key).
    cik: String
    /// The composite OpenFIGI number for this ticker. Find more information [here](https://www.openfigi.com/about/figi)
    composite_figi: String
    /// The name of the currency that this asset is traded with.
    currency_name: String
    /// The ISO 4217 code of the currency that this asset is traded with.
    currency_symbol: String
    /// The last date that the asset was traded.
    @timestampFormat("date-time")
    delisted_utc: Timestamp
    /// The information is accurate up to this time.
    @timestampFormat("date-time")
    last_updated_utc: Timestamp
    @required
    locale: Locale
    @required
    market: ListTickers200BodyApplicationJsonResultsItemMarket
    /// The name of the asset. For stocks/equities this will be the companies registered name. For crypto/fx this will be the name of the currency or coin pair.
    @required
    name: String
    /// The ISO code of the primary listing exchange for this asset.
    primary_exchange: String
    /// The share Class OpenFIGI number for this ticker. Find more information [here](https://www.openfigi.com/about/figi)
    share_class_figi: String
    /// The exchange symbol that this item is traded under.
    @required
    ticker: String
    /// The type of the asset. Find the types that we support via our [Ticker Types API](https://massive.com/docs/rest/stocks/tickers/ticker-types).
    type: String
}

@error("client")
@httpError(401)
structure ListTickers401 {}

structure ListTickersInput {
    /// Specify a ticker symbol.
    /// Defaults to empty string which queries all tickers.
    @openapiExtensions(
        "x-polygon-filter-field": {
            range: true
        }
    )
    @httpQuery("ticker")
    ticker: String
    /// Specify the type of the tickers. Find the types that we support via our [Ticker Types API](https://massive.com/docs/rest/stocks/tickers/ticker-types).
    /// Defaults to empty string which queries all types.
    @httpQuery("type")
    type: Type
    /// Filter by market type. By default all markets are included.
    @httpQuery("market")
    market: ListTickersInputMarket
    /// Specify the asset's primary exchange Market Identifier Code (MIC) according to [ISO 10383](https://www.iso20022.org/market-identifier-codes).
    /// Defaults to empty string which queries all exchanges.
    @httpQuery("exchange")
    exchange: String
    /// Specify the CUSIP code of the asset you want to search for. Find more information about CUSIP codes [at their website](https://www.cusip.com/identifiers.html#/CUSIP).
    /// Defaults to empty string which queries all CUSIPs.
    ///
    /// Note: Although you can query by CUSIP, due to legal reasons we do not return the CUSIP in the response.
    @httpQuery("cusip")
    cusip: String
    /// Specify the CIK of the asset you want to search for. Find more information about CIK codes [at their website](https://www.sec.gov/edgar/searchedgar/cik.htm).
    /// Defaults to empty string which queries all CIKs.
    @httpQuery("cik")
    cik: String
    /// Specify a point in time to retrieve tickers available on that date.
    /// Defaults to the most recent available date.
    @dateFormat
    @httpQuery("date")
    date: String
    /// Search for terms within the ticker and/or company name.
    @httpQuery("search")
    search: String
    /// Specify if the tickers returned should be actively traded on the queried date. Default is true.
    @httpQuery("active")
    active: Boolean
    /// Range by ticker.
    @httpQuery("ticker.gte")
    tickergte: String
    /// Range by ticker.
    @httpQuery("ticker.gt")
    tickergt: String
    /// Range by ticker.
    @httpQuery("ticker.lte")
    tickerlte: String
    /// Range by ticker.
    @httpQuery("ticker.lt")
    tickerlt: String
    /// Order results based on the `sort` field.
    @httpQuery("order")
    order: Order
    /// Limit the number of results returned, default is 100 and max is 1000.
    @dataExamples([
        {
            json: 100
        }
    ])
    @httpQuery("limit")
    @range(
        min: 1
        max: 1000
    )
    limit: Integer
    /// Sort field used for ordering.
    @httpQuery("sort")
    sort: Sort
}

@contentTypeDiscriminated
union ListTickers200Body {
    @contentType("application/json")
    applicationJson: ApplicationJson
    @contentType("text/csv")
    textCsv: String
}

/// The results for this request.
list GetStocksV1Dividends200BodyResults {
    member: GetStocksV1Dividends200BodyResultsItem
}

/// The results for this request.
list GetStocksV1Splits200BodyResults {
    member: GetStocksV1Splits200BodyResultsItem
}

/// An array of tickers that match your query.
///
/// Note: Although you can query by CUSIP, due to legal reasons we do not return the CUSIP in the response.
list ListTickers200BodyApplicationJsonResults {
    member: ListTickers200BodyApplicationJsonResultsItem
}

enum AdjustmentType {
    forward_split
    reverse_split
    stock_dividend
}

enum AdjustmentTypeAnyOf {
    forward_split
    reverse_split
    stock_dividend
}

enum DistributionType {
    recurring
    special
    supplemental
    irregular
    unknown
}

enum DistributionTypeAnyOf {
    recurring
    special
    supplemental
    irregular
    unknown
}

/// The status of this request's response.
enum GetStocksV1Dividends200BodyStatus {
    OK
}

/// The status of this request's response.
enum GetStocksV1Dividends400BodyStatus {
    ERROR
}

/// The status of this request's response.
enum GetStocksV1Splits200BodyStatus {
    OK
}

/// The status of this request's response.
enum GetStocksV1Splits400BodyStatus {
    ERROR
}

/// The market type of the asset.
enum ListTickers200BodyApplicationJsonResultsItemMarket {
    stocks
    crypto
    fx
    otc
    indices
}

enum ListTickersInputMarket {
    stocks
    crypto
    fx
    otc
    indices
}

/// The locale of the asset.
enum Locale {
    us
    global
}

enum Order {
    asc
    desc
}

enum Sort {
    ticker
    name
    market
    locale
    primary_exchange
    type
    currency_symbol
    currency_name
    base_currency_symbol
    base_currency_name
    cik
    composite_figi
    share_class_figi
    last_updated_utc
    delisted_utc
}

enum Type {
    CS
    ADRC
    ADRP
    ADRR
    UNIT
    RIGHT
    PFD
    FUND
    SP
    WARRANT
    INDEX
    ETF
    ETN
    OS
    GDR
    OTHER
    NYRS
    AGEN
    EQLK
    BOND
    ADRW
    BASKET
    LT
}
