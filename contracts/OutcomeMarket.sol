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
/// @notice On-chain LMSR prediction markets with oracle resolution.
///
///      Resolution flow (UMA-style):
///        1. Market closes at `closesAt`
///        2. Oracle proposes resolution via `oracleResolve(marketId, winner)`
///        3. Owner has 2h dispute window to call `disputeResolution(marketId)`
///        4. If no dispute, resolution is final after 2h
///        5. If deadline passes without oracle resolution, owner can override
///           via `ownerResolve(marketId, winner)` or anyone can `cancelExpired()`
///
///      Trust model:
///        - Oracle is trusted to resolve honestly (set by owner)
///        - Owner can dispute oracle within 2h (emergency brake)
///        - After 2h window, resolution is immutable
///        - Expired markets (>24h past close) can be cancelled by anyone
contract OutcomeMarket is ERC1155Supply, Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    IERC20 public immutable usdc;

    uint8 public constant MAX_OUTCOMES = 8;
    uint256 public constant MIN_SUBSIDY_MICRO = 10e6; // $10
    uint256 public constant ID_STRIDE = 256;
    uint64 public constant DISPUTE_WINDOW = 2 hours;
    uint64 public constant EXPIRY_WINDOW = 24 hours;

    address public oracle;

    struct Market {
        uint8 numOutcomes;
        bool resolved;
        uint8 winningOutcome;
        uint64 closesAt;
        int256 b;
        address creator;
        uint256 escrowMicro;
        // Oracle resolution fields
        uint64 resolvedAt;     // timestamp of oracle/owner resolution
        bool disputed;         // owner disputed oracle resolution
        bool cancelled;        // market cancelled (expired without resolution)
    }

    mapping(uint256 => Market) public markets;
    uint256 public nextMarketId = 1;

    // --- Events ---
    event MarketCreated(uint256 indexed marketId, address indexed creator, uint8 numOutcomes, uint256 subsidyMicro, int256 b, uint64 closesAt);
    event Traded(uint256 indexed marketId, address indexed trader, uint8 outcomeIdx, bool isBuy, uint256 shares, uint256 usdcMicro);
    event SetMinted(uint256 indexed marketId, address indexed who, uint256 amountMicro);
    event SetBurned(uint256 indexed marketId, address indexed who, uint256 amountMicro);
    event OracleResolved(uint256 indexed marketId, address indexed oracle, uint8 winningOutcome);
    event OwnerResolved(uint256 indexed marketId, uint8 winningOutcome);
    event ResolutionDisputed(uint256 indexed marketId, address indexed by);
    event MarketCancelled(uint256 indexed marketId);
    event Redeemed(uint256 indexed marketId, address indexed holder, uint256 shares, uint256 usdcMicro);
    event OracleUpdated(address indexed oldOracle, address indexed newOracle);

    // --- Errors ---
    error BadOutcomeCount();
    error SubsidyTooSmall();
    error ClosesInPast();
    error UnknownMarket();
    error AlreadyResolved();
    error AlreadyCancelled();
    error NotResolved();
    error MarketNotClosed();
    error BadOutcomeIndex();
    error SlippageExceeded(uint256 got, uint256 wanted);
    error NothingToRedeem();
    error NotOracle();
    error NotOwnerOrOracle();
    error NotDisputeWindow();
    error DisputeWindowExpired();
    error MarketNotExpired();

    modifier onlyOracleOrOwner() {
        if (msg.sender != oracle && msg.sender != owner()) revert NotOwnerOrOracle();
        _;
    }

    constructor(address usdcAddress, address initialOwner) ERC1155("") Ownable(initialOwner) {
        usdc = IERC20(usdcAddress);
        oracle = initialOwner; // default: owner is oracle
    }

    // ---------------------------------------------------------------------
    // Admin
    // ---------------------------------------------------------------------

    function setOracle(address newOracle) external onlyOwner {
        emit OracleUpdated(oracle, newOracle);
        oracle = newOracle;
    }

    // ---------------------------------------------------------------------
    // Market creation
    // ---------------------------------------------------------------------

    function createMarket(uint8 numOutcomes, uint256 subsidyMicro, uint64 closesAt) external returns (uint256 marketId) {
        if (numOutcomes < 2 || numOutcomes > MAX_OUTCOMES) revert BadOutcomeCount();
        if (subsidyMicro < MIN_SUBSIDY_MICRO) revert SubsidyTooSmall();
        if (closesAt <= block.timestamp) revert ClosesInPast();

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
            escrowMicro: subsidyMicro,
            resolvedAt: 0,
            disputed: false,
            cancelled: false
        });

        usdc.safeTransferFrom(msg.sender, address(this), subsidyMicro);
        emit MarketCreated(marketId, msg.sender, numOutcomes, subsidyMicro, b, closesAt);
    }

    // ---------------------------------------------------------------------
    // Complete sets
    // ---------------------------------------------------------------------

    function mintCompleteSet(uint256 marketId, uint256 amountMicro) external nonReentrant {
        Market storage m = _existingMarket(marketId);
        if (m.cancelled) revert AlreadyCancelled();
        usdc.safeTransferFrom(msg.sender, address(this), amountMicro);
        m.escrowMicro += amountMicro;
        for (uint8 i = 0; i < m.numOutcomes; i++) {
            _mint(msg.sender, _tokenId(marketId, i), amountMicro, "");
        }
        emit SetMinted(marketId, msg.sender, amountMicro);
    }

    function burnCompleteSet(uint256 marketId, uint256 amountMicro) external nonReentrant {
        Market storage m = _existingMarket(marketId);
        if (m.cancelled) revert AlreadyCancelled();
        for (uint8 i = 0; i < m.numOutcomes; i++) {
            _burn(msg.sender, _tokenId(marketId, i), amountMicro);
        }
        m.escrowMicro -= amountMicro;
        usdc.safeTransfer(msg.sender, amountMicro);
        emit SetBurned(marketId, msg.sender, amountMicro);
    }

    // ---------------------------------------------------------------------
    // Trading
    // ---------------------------------------------------------------------

    function buy(uint256 marketId, uint8 outcomeIdx, uint256 shares, uint256 maxCostMicro)
        external nonReentrant returns (uint256 costMicro)
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
        external nonReentrant returns (uint256 proceedsMicro)
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

    function priceOf(uint256 marketId, uint8 outcomeIdx) external view returns (uint256 price18) {
        Market storage m = _existingMarket(marketId);
        if (outcomeIdx >= m.numOutcomes) revert BadOutcomeIndex();
        SD59x18 p = LMSR.price(_currentQ(marketId, m.numOutcomes), m.b, outcomeIdx);
        return uint256(SD59x18.unwrap(p));
    }

    // ---------------------------------------------------------------------
    // Resolution — oracle-first with owner dispute window
    // ---------------------------------------------------------------------

    /// @notice Oracle resolves the market. Owner has 2h to dispute.
    function oracleResolve(uint256 marketId, uint8 winningOutcome) external {
        if (msg.sender != oracle) revert NotOracle();
        Market storage m = _existingMarket(marketId);
        if (m.resolved) revert AlreadyResolved();
        if (m.cancelled) revert AlreadyCancelled();
        if (block.timestamp < m.closesAt) revert MarketNotClosed();
        if (winningOutcome >= m.numOutcomes) revert BadOutcomeIndex();

        m.resolved = true;
        m.winningOutcome = winningOutcome;
        m.resolvedAt = uint64(block.timestamp);
        emit OracleResolved(marketId, msg.sender, winningOutcome);
    }

    /// @notice Owner disputes oracle resolution within 2h window. Reverts to open.
    function disputeResolution(uint256 marketId) external onlyOwner {
        Market storage m = _existingMarket(marketId);
        if (!m.resolved) revert NotResolved();
        if (m.resolvedAt == 0) revert NotResolved();
        if (block.timestamp > m.resolvedAt + DISPUTE_WINDOW) revert DisputeWindowExpired();
        if (m.cancelled) revert AlreadyCancelled();

        m.resolved = false;
        m.winningOutcome = 0;
        m.resolvedAt = 0;
        m.disputed = true;
        emit ResolutionDisputed(marketId, msg.sender);
    }

    /// @notice Owner resolves directly (fallback if oracle doesn't act within 24h).
    function ownerResolve(uint256 marketId, uint8 winningOutcome) external onlyOwner {
        Market storage m = _existingMarket(marketId);
        if (m.resolved) revert AlreadyResolved();
        if (m.cancelled) revert AlreadyCancelled();
        if (block.timestamp < m.closesAt) revert MarketNotClosed();
        if (winningOutcome >= m.numOutcomes) revert BadOutcomeIndex();

        m.resolved = true;
        m.winningOutcome = winningOutcome;
        m.resolvedAt = uint64(block.timestamp);
        emit OwnerResolved(marketId, winningOutcome);
    }

    /// @notice Cancel expired market (>24h past close, no resolution). Refund all holders.
    function cancelExpired(uint256 marketId) external nonReentrant {
        Market storage m = _existingMarket(marketId);
        if (m.resolved) revert AlreadyResolved();
        if (m.cancelled) revert AlreadyCancelled();
        if (block.timestamp < m.closesAt + EXPIRY_WINDOW) revert MarketNotExpired();

        m.cancelled = true;
        emit MarketCancelled(marketId);

        // Refund pro-rata escrow to all holders
        uint256 escrow = m.escrowMicro;
        uint256 totalShares = 0;
        for (uint8 i = 0; i < m.numOutcomes; i++) {
            totalShares += totalSupply(_tokenId(marketId, i));
        }

        if (totalShares > 0 && escrow > 0) {
            for (uint8 i = 0; i < m.numOutcomes; i++) {
                uint256 tokenId = _tokenId(marketId, i);
                uint256 shares = totalSupply(tokenId);
                if (shares == 0) continue;
                uint256 refund = (escrow * shares) / totalShares;
                if (refund > 0) {
                    usdc.safeTransfer(m.creator, refund);
                }
            }
        }
        m.escrowMicro = 0;
    }

    // ---------------------------------------------------------------------
    // Redemption
    // ---------------------------------------------------------------------

    function redeem(uint256 marketId) external nonReentrant returns (uint256 payoutMicro) {
        Market storage m = _existingMarket(marketId);
        if (!m.resolved) revert NotResolved();
        if (m.cancelled) revert AlreadyCancelled();

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
        if (m.cancelled) revert AlreadyCancelled();
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
