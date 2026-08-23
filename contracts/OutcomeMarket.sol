// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC1155} from "@openzeppelin/contracts/token/ERC1155/ERC1155.sol";
import {ERC1155Supply} from "@openzeppelin/contracts/token/ERC1155/extensions/ERC1155Supply.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {SD59x18, convert, ln} from "prb-math/SD59x18.sol";
import {LMSR} from "./LMSR.sol";

/// @title OutcomeMarket
/// @notice On-chain LMSR prediction markets, settled in USDC, shares as ERC1155.
///
///      This is Phase 1+2 of the plan: self-custodied outcome shares (instead
///      of rows in Postgres) priced by an on-chain LMSR (instead of Python
///      Decimal math). It sits *next to* the existing off-chain bot/ledger.py
///      markets, not instead of them — the bot can quote/trade here for users
///      who want on-chain custody of their positions, exactly the way it
///      already quotes/trades against its own internal ledger today.
///
///      Trust model for `resolve()` is intentionally the same as the bot's
///      current ADMIN_TG_ID-gated resolution: owner-only. That's Phase 3's
///      job (propose + dispute window, or Chainlink for objective markets) —
///      called out explicitly so it isn't mistaken for a finished oracle.
///
///      Token id packing: `id = marketId * 256 + outcomeIdx`. Safe because
///      numOutcomes <= MAX_OUTCOMES (8) and marketId is a monotonic counter.
contract OutcomeMarket is ERC1155Supply, Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    IERC20 public immutable usdc;

    uint8 public constant MAX_OUTCOMES = 8;
    uint256 public constant MIN_SUBSIDY_MICRO = 10e6; // $10
    uint256 public constant ID_STRIDE = 256;

    struct Market {
        uint8 numOutcomes;
        bool resolved;
        uint8 winningOutcome;
        uint64 closesAt;
        int256 b; // LMSR liquidity parameter, USDC micro-units
        address creator;
        uint256 escrowMicro; // USDC actually held against this market
    }

    mapping(uint256 => Market) public markets;
    uint256 public nextMarketId = 1;

    event MarketCreated(
        uint256 indexed marketId, address indexed creator, uint8 numOutcomes, uint256 subsidyMicro, int256 b, uint64 closesAt
    );
    event Traded(
        uint256 indexed marketId, address indexed trader, uint8 outcomeIdx, bool isBuy, uint256 shares, uint256 usdcMicro
    );
    event SetMinted(uint256 indexed marketId, address indexed who, uint256 amountMicro);
    event SetBurned(uint256 indexed marketId, address indexed who, uint256 amountMicro);
    event Resolved(uint256 indexed marketId, uint8 winningOutcome);
    event Redeemed(uint256 indexed marketId, address indexed holder, uint256 shares, uint256 usdcMicro);

    error BadOutcomeCount();
    error SubsidyTooSmall();
    error ClosesInPast();
    error UnknownMarket();
    error AlreadyResolved();
    error NotResolved();
    error MarketNotClosed();
    error BadOutcomeIndex();
    error SlippageExceeded(uint256 got, uint256 wanted);
    error NothingToRedeem();

    constructor(address usdcAddress, address initialOwner) ERC1155("") Ownable(initialOwner) {
        usdc = IERC20(usdcAddress);
    }

    // ---------------------------------------------------------------------
    // Market creation
    // ---------------------------------------------------------------------

    /// @notice Create a market. `b = subsidy / ln(numOutcomes)` — the funding
    ///         theorem: escrow can never fall below the worst-case payout as
    ///         long as every trade goes through buy()/sell()/mintCompleteSet().
    function createMarket(uint8 numOutcomes, uint256 subsidyMicro, uint64 closesAt) external returns (uint256 marketId) {
        if (numOutcomes < 2 || numOutcomes > MAX_OUTCOMES) revert BadOutcomeCount();
        if (subsidyMicro < MIN_SUBSIDY_MICRO) revert SubsidyTooSmall();
        if (closesAt <= block.timestamp) revert ClosesInPast();

        // subsidyMicro is already 1e6-scaled ($1 == 1e6); wrap it directly at
        // the 1e18 raw representation instead of routing through convert(),
        // which would treat it as a plain integer count and rescale again.
        SD59x18 subsidyWad = SD59x18.wrap(int256(subsidyMicro) * 1e12);
        SD59x18 lnN = ln(convert(int256(uint256(numOutcomes))));
        int256 b = SD59x18.unwrap(subsidyWad.div(lnN)) / 1e12;

        marketId = nextMarketId++;
        markets[marketId] = Market({
            numOutcomes: numOutcomes,
            resolved: false,
            winningOutcome: 0,
            closesAt: closesAt,
            b: b,
            creator: msg.sender,
            escrowMicro: subsidyMicro
        });

        usdc.safeTransferFrom(msg.sender, address(this), subsidyMicro);
        emit MarketCreated(marketId, msg.sender, numOutcomes, subsidyMicro, b, closesAt);
    }

    // ---------------------------------------------------------------------
    // Complete sets — always exactly 1:1 with USDC, no pricing, no oracle
    // ---------------------------------------------------------------------

    function mintCompleteSet(uint256 marketId, uint256 amountMicro) external nonReentrant {
        Market storage m = _existingMarket(marketId);
        usdc.safeTransferFrom(msg.sender, address(this), amountMicro);
        m.escrowMicro += amountMicro;
        for (uint8 i = 0; i < m.numOutcomes; i++) {
            _mint(msg.sender, _tokenId(marketId, i), amountMicro, "");
        }
        emit SetMinted(marketId, msg.sender, amountMicro);
    }

    function burnCompleteSet(uint256 marketId, uint256 amountMicro) external nonReentrant {
        Market storage m = _existingMarket(marketId);
        for (uint8 i = 0; i < m.numOutcomes; i++) {
            _burn(msg.sender, _tokenId(marketId, i), amountMicro);
        }
        m.escrowMicro -= amountMicro;
        usdc.safeTransfer(msg.sender, amountMicro);
        emit SetBurned(marketId, msg.sender, amountMicro);
    }

    // ---------------------------------------------------------------------
    // Trading — share-denominated with slippage protection
    // ---------------------------------------------------------------------

    function buy(uint256 marketId, uint8 outcomeIdx, uint256 shares, uint256 maxCostMicro)
        external
        nonReentrant
        returns (uint256 costMicro)
    {
        Market storage m = _tradeableMarket(marketId, outcomeIdx);
        int256[] memory q = _currentQ(marketId, m.numOutcomes);

        costMicro = LMSR.buyCost(q, m.b, outcomeIdx, shares);
        if (costMicro > maxCostMicro) revert SlippageExceeded(costMicro, maxCostMicro);

        usdc.safeTransferFrom(msg.sender, address(this), costMicro);
        m.escrowMicro += costMicro;
        _mint(msg.sender, _tokenId(marketId, outcomeIdx), shares, "");

        emit Traded(marketId, msg.sender, outcomeIdx, true, shares, costMicro);
    }

    function sell(uint256 marketId, uint8 outcomeIdx, uint256 shares, uint256 minProceedsMicro)
        external
        nonReentrant
        returns (uint256 proceedsMicro)
    {
        Market storage m = _tradeableMarket(marketId, outcomeIdx);
        int256[] memory q = _currentQ(marketId, m.numOutcomes);

        proceedsMicro = LMSR.sellProceeds(q, m.b, outcomeIdx, shares);
        if (proceedsMicro < minProceedsMicro) revert SlippageExceeded(proceedsMicro, minProceedsMicro);

        _burn(msg.sender, _tokenId(marketId, outcomeIdx), shares);
        m.escrowMicro -= proceedsMicro;
        usdc.safeTransfer(msg.sender, proceedsMicro);

        emit Traded(marketId, msg.sender, outcomeIdx, false, shares, proceedsMicro);
    }

    // ---------------------------------------------------------------------
    // Read-only quotes
    // ---------------------------------------------------------------------

    function quoteBuy(uint256 marketId, uint8 outcomeIdx, uint256 shares) external view returns (uint256) {
        Market storage m = _existingMarket(marketId);
        if (outcomeIdx >= m.numOutcomes) revert BadOutcomeIndex();
        return LMSR.buyCost(_currentQ(marketId, m.numOutcomes), m.b, outcomeIdx, shares);
    }

    function quoteSell(uint256 marketId, uint8 outcomeIdx, uint256 shares) external view returns (uint256) {
        Market storage m = _existingMarket(marketId);
        if (outcomeIdx >= m.numOutcomes) revert BadOutcomeIndex();
        return LMSR.sellProceeds(_currentQ(marketId, m.numOutcomes), m.b, outcomeIdx, shares);
    }

    /// @return price18 implied probability as an 18-decimal fraction (0.35e18 = 35%)
    function priceOf(uint256 marketId, uint8 outcomeIdx) external view returns (uint256 price18) {
        Market storage m = _existingMarket(marketId);
        if (outcomeIdx >= m.numOutcomes) revert BadOutcomeIndex();
        SD59x18 p = LMSR.price(_currentQ(marketId, m.numOutcomes), m.b, outcomeIdx);
        return uint256(SD59x18.unwrap(p));
    }

    // ---------------------------------------------------------------------
    // Resolution — owner-gated (Phase 3: oracle/dispute window)
    // ---------------------------------------------------------------------

    function resolve(uint256 marketId, uint8 winningOutcome) external onlyOwner {
        Market storage m = _existingMarket(marketId);
        if (m.resolved) revert AlreadyResolved();
        if (block.timestamp < m.closesAt) revert MarketNotClosed();
        if (winningOutcome >= m.numOutcomes) revert BadOutcomeIndex();

        m.resolved = true;
        m.winningOutcome = winningOutcome;
        emit Resolved(marketId, winningOutcome);
    }

    function redeem(uint256 marketId) external nonReentrant returns (uint256 payoutMicro) {
        Market storage m = _existingMarket(marketId);
        if (!m.resolved) revert NotResolved();

        uint256 tokenId = _tokenId(marketId, m.winningOutcome);
        payoutMicro = balanceOf(msg.sender, tokenId);
        if (payoutMicro == 0) revert NothingToRedeem();

        _burn(msg.sender, tokenId, payoutMicro);
        m.escrowMicro -= payoutMicro;
        usdc.safeTransfer(msg.sender, payoutMicro);

        emit Redeemed(marketId, msg.sender, payoutMicro, payoutMicro);
    }

    // ---------------------------------------------------------------------
    // Internal helpers
    // ---------------------------------------------------------------------

    function _tokenId(uint256 marketId, uint8 outcomeIdx) internal pure returns (uint256) {
        return marketId * ID_STRIDE + outcomeIdx;
    }

    function _existingMarket(uint256 marketId) internal view returns (Market storage m) {
        m = markets[marketId];
        if (m.creator == address(0)) revert UnknownMarket();
    }

    function _tradeableMarket(uint256 marketId, uint8 outcomeIdx) internal view returns (Market storage m) {
        m = _existingMarket(marketId);
        if (m.resolved) revert AlreadyResolved();
        if (block.timestamp >= m.closesAt) revert MarketNotClosed();
        if (outcomeIdx >= m.numOutcomes) revert BadOutcomeIndex();
    }

    function _currentQ(uint256 marketId, uint8 numOutcomes) internal view returns (int256[] memory q) {
        q = new int256[](numOutcomes);
        for (uint8 i = 0; i < numOutcomes; i++) {
            q[i] = int256(totalSupply(_tokenId(marketId, i)));
        }
    }
}
